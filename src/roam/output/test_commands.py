"""Build affected-test commands using runner evidence for each test file."""

from __future__ import annotations

import ast
import json
import os
import re
import shlex
import tomllib
from configparser import ConfigParser
from configparser import Error as IniError
from pathlib import Path, PurePosixPath

from roam._glob_match import matches_glob
from roam.index.parser import detect_language
from roam.languages.registry import JS_FAMILY_LANGUAGES
from roam.parser_pack import DownloadError, LanguageNotFoundError, get_parser

_JS_COMMANDS = {
    "vitest": "npx vitest run",
    "jest": "npx jest --runTestsByPath",
    "mocha": "npx mocha",
    "playwright": "npx playwright test",
}
_MANIFESTS = {
    "python": ("pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg"),
    "go": ("go.mod",),
    "rust": ("Cargo.toml",),
    "ruby": ("Gemfile",),
}
_WINDOWS = os.name == "nt"
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


def _script_invocations(script):
    """Yield executables and arguments, discarding common package-manager wrappers."""
    for command in _script_commands(script):
        while command and (re.match(r"[a-zA-Z_][a-zA-Z0-9_]*=", command[0]) or command[0] in {"env", "cross-env"}):
            command.pop(0)
        if command and command[0] in {"npx", "pnpm", "npm", "yarn", "bun", "bunx"}:
            launcher = command.pop(0)
            if command[:1] == ["run"] and launcher in {"npm", "pnpm", "yarn", "bun"}:
                yield "run", command[1:]
                continue
            if launcher == "npm" and command[:1] != ["exec"]:
                continue
            if command and command[0] in {"exec", "dlx", "x"}:
                command.pop(0)
            while command and command[0].startswith("-"):
                command.pop(0)
        if command:
            yield PurePosixPath(command[0]).name, command[1:]


def _script_runner(invocations):
    runners = {
        runner
        for runner, args in (invocations or ())
        if runner in _JS_COMMANDS
        and (runner != "playwright" or args[:1] == ["test"])
        and not {"--help", "--version"}.intersection(args)
    }
    return next(iter(runners)) if len(runners) == 1 else False


def _literal_strings(node):
    nodes = node.named_children if node.type == "array" else [node]
    try:
        return [ast.literal_eval(child.text.decode("utf-8")) for child in nodes if child.type == "string"]
    except SyntaxError as exc:
        raise ValueError("unsupported JavaScript string literal") from exc


def _js_config_scope(path, runner):
    """Read static include and exclude settings without executing the config."""
    source = path.read_bytes()
    try:
        tree = get_parser("typescript").parse(source)
    except (DownloadError, LanguageNotFoundError, LookupError) as exc:
        raise ValueError(f"cannot load test configuration parser: {path.name}") from exc
    if tree is None or tree.root_node.has_error:
        raise ValueError(f"cannot parse test configuration: {path.name}")
    settings = {
        "playwright": {"testDir", "testMatch", "testIgnore"},
        "jest": {"roots", "testMatch", "testRegex", "testPathIgnorePatterns"},
        "vitest": {"include", "exclude"},
    }[runner]
    scope = {}
    pending = [tree.root_node]
    while pending:
        node = pending.pop()
        if node.type == "pair":
            key = node.child_by_field_name("key").text.decode("utf-8").strip("'\"")
            parent = node.parent.parent
            vitest_test_settings = (
                parent.type == "pair" and parent.child_by_field_name("key").text.decode("utf-8").strip("'\"") == "test"
            )
            if key in settings and (runner != "vitest" or vitest_test_settings):
                value = node.child_by_field_name("value")
                literals = _literal_strings(value)
                if (
                    key in scope
                    or (not literals and value.type != "array")
                    or (value.type == "array" and len(literals) != len(value.named_children))
                ):
                    raise ValueError(f"non-static {key} in {path.name}")
                scope[key] = literals
        pending.extend(node.named_children)
    return scope


def _read_js_evidence(base):
    try:
        package = json.loads((base / "package.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        package = {}
    if not isinstance(package, dict):
        raise ValueError("package.json must be an object")
    scripts = package.get("scripts", {})
    dependencies = package.get("devDependencies", {})
    if not isinstance(scripts, dict) or not isinstance(dependencies, dict):
        raise ValueError("invalid package.json runner metadata")
    test_scripts = {}
    for name, script in scripts.items():
        if name in {"test", "e2e"} or name.startswith("test:"):
            if not isinstance(script, str):
                raise ValueError("test scripts must be strings")
            try:
                test_scripts[name] = list(_script_invocations(script))
            except ValueError:
                # An unsupported auxiliary script only affects files in its scope.
                test_scripts[name] = None
    scopes = {}
    for runner in ("playwright", "jest", "vitest", "vite"):
        for ext in ("ts", "js", "mts", "mjs", "cts", "cjs"):
            config = base / f"{runner}.config.{ext}"
            try:
                config.stat()
            except FileNotFoundError:
                continue
            kind = "vitest" if runner == "vite" else runner
            if kind not in scopes:
                try:
                    scope = _js_config_scope(config, kind)
                except (OSError, RuntimeError, ValueError, AttributeError):
                    # An unreadable or dynamic config only blocks its own runner.
                    scope = None
                if runner != "vite" or scope is None or scope:
                    scopes[kind] = scope
            break
    fallback = {name for name in _JS_COMMANDS if isinstance(dependencies.get(name), str)}
    if isinstance(dependencies.get("@playwright/test"), str):
        fallback.add("playwright")
    return test_scripts, scopes, fallback


def _match_test_glob(file, pattern):
    pattern = pattern.removeprefix("<rootDir>/").removeprefix("./")
    if PurePosixPath(pattern).is_absolute() or pattern.startswith("!"):
        return False
    return matches_glob(file, pattern)


def _config_allows(file, scope):
    roots = scope.get("testDir", scope.get("roots"))
    if roots is not None and not any(
        (root := PurePosixPath(item.removeprefix("<rootDir>/"))) == PurePosixPath(".")
        or root in PurePosixPath(file).parents
        for item in roots
    ):
        return False
    includes = scope.get("testMatch", scope.get("include"))
    if includes is not None and not any(_match_test_glob(file, pattern) for pattern in includes):
        return False
    if any(_match_test_glob(file, pattern) for pattern in scope.get("testIgnore", scope.get("exclude", []))):
        return False
    if any(re.search(pattern, file) for pattern in scope.get("testPathIgnorePatterns", [])):
        return False
    if scope.get("testRegex"):
        return any(re.search(pattern, file) for pattern in scope["testRegex"])
    return True


def _script_patterns(invocations):
    patterns = []
    for _, args in invocations or ():
        words = iter(args)
        for arg in words:
            if arg in {"-c", "--config", "--reporter", "--project", "--root", "--dir", "--grep", "--testNamePattern"}:
                next(words, None)
            elif not arg.startswith("-") and (
                "/" in arg or any(char in arg for char in "*?{") or detect_language(arg) in JS_FAMILY_LANGUAGES
            ):
                patterns.append(arg)
    return patterns


def _script_scope(file, name, invocations):
    # A positional path or glob is stronger evidence than a script-name convention.
    if patterns := _script_patterns(invocations):
        return any(
            _match_test_glob(file, pattern) or PurePosixPath(pattern) in PurePosixPath(file).parents
            for pattern in patterns
        )
    labels = set(re.split(r"[:_-]", name)) - {"test"}
    return bool(labels.intersection(re.split(r"[/._-]", file)))


def _only_runner(runners):
    if len(runners) == 1:
        return next(iter(runners))
    return False if runners else None


def _js_runner(evidence, file):
    scripts, scopes, fallback = evidence
    explicit = [invocations for name, invocations in scripts.items() if _script_scope(file, name, invocations)]
    configured = {
        runner
        for runner, scope in scopes.items()
        if scope
        and scope.keys() & {"testDir", "testMatch", "roots", "testRegex", "include"}
        and _config_allows(file, scope)
    }
    if explicit:
        runner = _only_runner({_script_runner(invocations) for invocations in explicit})
    elif configured:
        runner = _only_runner(configured)
    elif "test" in scripts:
        default = scripts["test"]
        if default and len(default) == 1:
            executable, args = default[0]
            if executable in {"nr", "run"} and len(args) == 1 and args[0] != "test":
                default = scripts.get(args[0], default)
        runner = (
            False if _script_patterns(default) and not _script_scope(file, "test", default) else _script_runner(default)
        )
    elif scripts:
        runner = (
            _only_runner(
                {_script_runner(invocations) for invocations in scripts.values() if not _script_patterns(invocations)}
            )
            or False
        )
    else:
        runner = _only_runner(fallback | scopes.keys())
    if runner in scopes:
        scope = scopes[runner]
        if scope is None or not _config_allows(file, scope):
            return False
    return runner


def _python_runner(content):
    project = tomllib.loads(content)
    tools = project.get("tool", {})
    if isinstance(tools.get("pytest"), dict):
        return "pytest"
    poetry = tools.get("poetry", {})
    poetry_groups = [poetry.get("dependencies", {}), poetry.get("dev-dependencies", {})]
    poetry_groups.extend(group.get("dependencies", {}) for group in poetry.get("group", {}).values())
    if any(dep.casefold() == "pytest" for group in poetry_groups for dep in group):
        return "pytest"
    metadata = project.get("project", {})
    groups = [
        metadata.get("dependencies", []),
        *metadata.get("optional-dependencies", {}).values(),
        *project.get("dependency-groups", {}).values(),
        *(env.get("dependencies", []) for env in tools.get("hatch", {}).get("envs", {}).values()),
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
    bundled = False
    for name in _MANIFESTS.get(language, ()):
        try:
            content = (base / name).read_text(encoding="utf-8")
        except FileNotFoundError:
            continue
        if name == "pyproject.toml":
            if runner := _python_runner(content):
                return runner
            continue
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
            bundled = True
            if re.search(r"""^\s*gem\s*\(?\s*["']rspec(?:-core|-rails)?["']""", content, re.MULTILINE):
                return "rspec"
            if not re.search(r"^\s*gemspec(?:[\s(]|$)", content, re.MULTILINE):
                return None
            break
        config = ConfigParser(interpolation=None)
        config.read_string(content)
        if name == "pytest.ini" or config.has_section("tool:pytest" if name == "setup.cfg" else "pytest"):
            return "pytest"

    if language == "ruby":
        for spec in sorted(base.glob("*.gemspec")):
            if re.search(
                r"""^\s*\w+\.add_(?:(?:development|runtime)_)?dependency\s*\(?\s*["']rspec(?:-core|-rails)?["']""",
                spec.read_text(encoding="utf-8"),
                re.MULTILINE,
            ):
                return "rspec" if bundled else "rspec-standalone"
    if language == "python":
        try:
            with (base / "conftest.py").open("rb"):
                conftest = True
        except FileNotFoundError:
            conftest = False
        requirements = [
            path
            for pattern in ("requirements*.txt", "requirements*.in", "requirements/*.txt", "requirements/*.in")
            for path in base.glob(pattern)
        ]
        for req in sorted(requirements):
            if any(_PYTEST_DEPENDENCY.match(line.strip()) for line in req.read_text(encoding="utf-8").splitlines()):
                return "pytest"
        if conftest:
            return "pytest-conftest"
    return None


def affected_test_commands(root: Path, test_files: list[str]) -> tuple[list[dict], list[str]]:
    """Return commands with their test files, plus paths with unknown runners."""
    cache = {}
    groups = {}
    unknown = []
    for path in test_files:
        runner = None
        conftest = False
        file = PurePosixPath(path)
        if not file.name or file.is_absolute() or ".." in file.parts:
            unknown.append(path)
            continue
        language = detect_language(path)
        if language in JS_FAMILY_LANGUAGES:
            language = "js"
        for directory in file.parents:
            key = (directory, language)
            if key not in cache:
                try:
                    cache[key] = (
                        _read_js_evidence(root / directory)
                        if language == "js"
                        else _configured_runner(root / directory, language)
                    )
                except (OSError, ValueError, AttributeError, IniError):
                    cache[key] = False  # Unusable local evidence precludes an ancestor fallback.
            runner = cache[key]
            if language == "js" and runner is not False:
                try:
                    runner = _js_runner(runner, file.relative_to(directory).as_posix())
                except (ValueError, re.error):
                    runner = False
            if runner is False:
                break
            if runner == "pytest-conftest":
                conftest = True
                runner = None
                continue
            if runner:
                groups.setdefault((directory, runner), []).append(path)
                break
        if runner is None and conftest:
            groups.setdefault((PurePosixPath("."), "pytest"), []).append(path)
        elif not runner:
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
        elif runner in {"rspec", "rspec-standalone"}:
            command = " ".join(["bundle exec rspec" if runner == "rspec" else "rspec", *args])
            runner = "rspec"
        elif runner == "playwright":
            command = (
                _JS_COMMANDS[runner]
                + " "
                + " ".join(
                    shlex.quote((r"(^|/)" + re.escape(path)[1:] if path.startswith("-") else re.escape(path)) + "$")
                    for path in relative
                )
            )
        else:
            command = " ".join([_JS_COMMANDS.get(runner, runner), *args])
        if directory != PurePosixPath("."):
            command = f"(cd {shlex.quote('./' + directory.as_posix())} && {command})"
        commands.append({"runner": runner, "command": command, "shell": "posix", "files": paths})
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


def test_run_lines(fields: dict, *, command_only: bool = False) -> list[str]:
    """Return the text lines that tell a reader how to run the tests."""
    commands = [entry["command"] for entry in fields.get("test_commands", [])]
    if not _WINDOWS or not commands:
        return commands
    if command_only:
        return ["# Run in a POSIX shell", *commands]
    return [f"{command} (POSIX shell)" for command in commands]


def unknown_runner_message(paths: list[str]) -> str:
    """Render paths safely for command-mode comments and text output."""
    escaped = (path.replace("\n", "\\n").replace("\r", "\\r") for path in paths)
    return "Unknown test runner for: " + ", ".join(escaped)
