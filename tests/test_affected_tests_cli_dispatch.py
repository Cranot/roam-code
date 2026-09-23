"""``affected-tests`` through a string-keyed command registry.

A registry such as ``_COMMANDS = {"alpha": ("roam.commands.cmd_alpha",
"alpha_cmd")}`` gets a ``dispatch`` edge to every handler it names. The
reverse walk used to continue through the registry to the dispatcher and
from there to every test that invokes *any* command, so each command's
selection became every CLI test in the repository. Meanwhile a test that
drives the command by its registered name (``CliRunner().invoke(cli,
["alpha"])``) or imports its module without calling the handler had no
edge of its own and was selected only as part of that flood, if at all.

The walk now stops at the registry. Past it a test is excluded only when
the analysis proves it cannot run the handler: a proven run of the command
(the key in command position of an argv, in process or in a subprocess) is
CLI_INVOKE, a proven run of every entry stays TRANSITIVE, and anything the
analysis cannot resolve (a computed key, a key that is only read, a key
after the command, an unresolved argv) is CLI_POSSIBLE.

The fixture package is named ``roam`` because the registry-dispatch
indexer pass only resolves tuples under the project's package prefix.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
from click.testing import CliRunner

from tests.conftest import invoke_cli

_OTHER_COMMANDS = [f"cmd{i:02d}" for i in range(12)]


def _command_module(fn_name: str) -> str:
    return (
        "import click\n\n\n"
        '@click.command()\n@click.argument("rest", nargs=-1)\n'
        f"def {fn_name}(rest):\n    click.echo('{fn_name}')\n"
    )


_INVOKE_HEADER = "from click.testing import CliRunner\n\nfrom roam.cli import cli\n\n\n"
_SUBPROCESS_HEADER = "import subprocess\nimport sys\n\n\n"

# Fixture tests for the fail-safe rule.  Each one that claims to run a
# handler asserts that handler's output.
_PROPERTY_FILES = {
    # Alternatives: a literal beside a fixture-chosen key.
    "tests/test_p1_literal_and_fixture.py": (
        "import pytest\n" + _INVOKE_HEADER + "@pytest.fixture\ndef command():\n    return 'cmd02'\n\n\n"
        "def test_alpha_and_fixture_command(command):\n"
        "    runner = CliRunner()\n"
        "    outputs = []\n"
        '    for key in ["alpha", command]:\n'
        "        outputs.append(runner.invoke(cli, [key]).output)\n"
        '    assert "alpha-ran" in outputs[0] and "cmd02_cmd" in outputs[1], outputs\n'
    ),
    # Alternatives, harder: the other alternative is a helper parameter.
    "tests/test_p1_helper_alternatives.py": (
        _INVOKE_HEADER + "def _run_each(extra):\n"
        '    return [CliRunner().invoke(cli, [key]).output for key in ["alpha", extra]]\n\n\n'
        "def test_alpha_and_cmd03():\n"
        '    outputs = _run_each("cmd03")\n'
        '    assert "alpha-ran" in outputs[0] and "cmd03_cmd" in outputs[1], outputs\n'
    ),
    # Controls: a parametrize list runs every case; one of two values runs one.
    "tests/test_p1_parametrized.py": (
        "import pytest\n" + _INVOKE_HEADER + '@pytest.mark.parametrize("command", ["alpha", "cmd05"])\n'
        "def test_each_case(command):\n"
        '    expected = "alpha-ran" if command == "alpha" else f"{command}_cmd"\n'
        "    assert expected in CliRunner().invoke(cli, [command]).output\n"
    ),
    "tests/test_p1_one_of.py": (
        "import os\n\n" + _INVOKE_HEADER + "def test_one_of_two():\n"
        '    key = "alpha" if os.environ.get("FIXTURE_PICK_ALPHA") else "cmd04"\n'
        '    assert "cmd04_cmd" in CliRunner().invoke(cli, [key]).output\n'
    ),
    # Reading an entry: a helper that returns the entry.
    "tests/test_p2_describe.py": (
        "from roam.cli import _COMMANDS\n\n\n"
        "def describe(name):\n    return _COMMANDS[name]\n\n\n"
        "def test_describe_alpha():\n"
        '    assert describe("alpha") == ("roam.commands.cmd_alpha", "alpha_cmd")\n'
    ),
    # Reading an entry, harder: the production loader reads the key the test passes.
    "tests/test_p2_load_only.py": (
        'from roam.cli import load_command\n\n\ndef test_alpha_loads():\n    assert load_command("alpha").name == "alpha"\n'
    ),
    # Control: the loaded entry is run.
    "tests/test_p2_load_and_invoke.py": (
        "from click.testing import CliRunner\n\nfrom roam.cli import load_command\n\n\n"
        "def test_alpha_loaded_and_run():\n"
        '    assert "alpha-ran" in CliRunner().invoke(load_command("alpha"), []).output\n'
    ),
    # Argv: argv read in order, inline or stored.
    "tests/test_p4_select_inline.py": (
        _INVOKE_HEADER + "def test_alpha_after_select():\n"
        '    assert "alpha-ran" in CliRunner().invoke(cli, ["--select", ".summary", "alpha"]).output\n'
    ),
    "tests/test_p4_select_stored.py": (
        _INVOKE_HEADER + "def test_alpha_after_stored_select():\n"
        '    argv = ["--select", ".summary", "alpha"]\n'
        '    assert "alpha-ran" in CliRunner().invoke(cli, argv).output\n'
    ),
    "tests/test_p4_command_then_key_inline.py": (
        _INVOKE_HEADER + "def test_cmd04_with_an_argument():\n"
        '    assert "cmd04_cmd" in CliRunner().invoke(cli, ["cmd04", "alpha"]).output\n'
    ),
    "tests/test_p4_command_then_key_stored.py": (
        _INVOKE_HEADER + "def test_cmd05_with_a_stored_argument():\n"
        '    args = ["cmd05", "alpha"]\n'
        '    assert "cmd05_cmd" in CliRunner().invoke(cli, args).output\n'
    ),
    # Argv, harder: a key-shaped option value, inline and through the
    # conftest ``invoke_cli`` shape (conditional append, then extend).
    "tests/test_p4_option_value_is_a_key.py": (
        _INVOKE_HEADER + "def test_cmd06_after_a_key_shaped_option_value():\n"
        '    assert "cmd06_cmd" in CliRunner().invoke(cli, ["--select", "cmd07", "cmd06"]).output\n'
    ),
    "tests/test_p4_helper_argv.py": (
        _INVOKE_HEADER + "def _invoke(args, json_mode=False):\n"
        "    full = []\n"
        "    if json_mode:\n"
        '        full.append("--json")\n'
        "    full.extend(args)\n"
        "    return CliRunner().invoke(cli, full)\n\n\n"
        "def test_cmd08_through_a_helper():\n"
        '    assert "cmd08_cmd" in _invoke(["--select", "cmd09", "cmd08"], json_mode=True).output\n'
    ),
    # The argv crosses two helpers before it reaches the dispatcher.
    "tests/test_p4_nested_helpers.py": (
        _INVOKE_HEADER + "def _invoke(args):\n"
        '    return CliRunner().invoke(cli, ["--json", *args])\n\n\n'
        "def _run(*args):\n    return _invoke(list(args))\n\n\n"
        "def test_cmd11_through_two_helpers():\n"
        '    assert "cmd11_cmd" in _run("cmd11").output\n'
    ),
    # Subprocesses: tests/test_w805_yyyy_... (a module-level
    # helper) and tests/test_w805_ddddd_... (a method, a local import, a
    # loop over git argvs).  Neither file imports the package.
    "tests/test_p5_subprocess.py": (
        _SUBPROCESS_HEADER + "def _run_alpha(path):\n"
        "    return subprocess.run(\n"
        '        [sys.executable, "-m", "roam", "--json", "alpha", str(path)],\n'
        "        capture_output=True,\n"
        "        text=True,\n"
        "    )\n\n\n"
        "def test_alpha_in_a_child_process(tmp_path):\n"
        "    proc = _run_alpha(tmp_path)\n"
        '    assert "alpha-ran" in proc.stdout, proc.stdout + proc.stderr\n'
    ),
    "tests/test_p5_method_local_import.py": (
        "import sys\n\n\n"
        "class TestAlpha:\n"
        "    def test_alpha_after_git(self, tmp_path):\n"
        "        import subprocess\n\n"
        "        for args in (\n"
        '            ["git", "init"],\n'
        '            ["git", "config", "user.name", "t"],\n'
        "        ):\n"
        "            subprocess.run(args, cwd=tmp_path, capture_output=True)\n"
        "        r = subprocess.run(\n"
        '            [sys.executable, "-m", "roam", "--json", "alpha"],\n'
        "            capture_output=True,\n"
        "            text=True,\n"
        "        )\n"
        '        assert "alpha-ran" in r.stdout, r.stdout + r.stderr\n'
    ),
    # Subprocesses, harder: a shell command string with an option value before the
    # command, and a helper whose key-shaped option value is consumed.
    "tests/test_p5_shell_string.py": (
        _SUBPROCESS_HEADER + "def test_alpha_in_a_shell():\n"
        "    proc = subprocess.run(\n"
        "        f'\"{sys.executable}\" -m roam --select .summary alpha', shell=True, capture_output=True, text=True\n"
        "    )\n"
        '    assert "alpha-ran" in proc.stdout, proc.stdout + proc.stderr\n'
    ),
    "tests/test_p5_helper.py": (
        _SUBPROCESS_HEADER + "def _roam(*args):\n"
        '    return subprocess.run([sys.executable, "-m", "roam", *args], capture_output=True, text=True)\n\n\n'
        "def test_cmd10_in_a_child_process():\n"
        '    assert "cmd10_cmd" in _roam("--select", "cmd11", "cmd10").stdout\n'
    ),
    # Controls: other programs that mention the package and a key (a shell may run anything).
    "tests/test_p5_not_roam.py": (
        "import os\n" + _SUBPROCESS_HEADER + "def test_other_programs_mention_alpha():\n"
        '    subprocess.run(["echo", "roam", "alpha"], check=True)\n'
    ),
    # ``python -c`` code that starts the package's entry point.
    "tests/test_p5_runpy.py": (
        _SUBPROCESS_HEADER + "def test_alpha_through_runpy():\n"
        "    code = \"import runpy, sys; sys.argv = ['roam', 'alpha']; runpy.run_module('roam', run_name='__main__')\"\n"
        '    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)\n'
        '    assert "alpha-ran" in proc.stdout, proc.stdout + proc.stderr\n'
    ),
    # An argv the analysis cannot see: selected, not proven.
    "tests/test_p5_unresolved.py": (
        "import pytest\n\n" + _SUBPROCESS_HEADER + "@pytest.fixture\ndef argv():\n"
        '    return [sys.executable, "-m", "roam", "alpha"]\n\n\n'
        "def test_argv_from_a_fixture(argv):\n"
        '    assert "alpha-ran" in subprocess.run(argv, capture_output=True, text=True).stdout\n'
    ),
}


def _files() -> dict[str, str]:
    registry = [
        '    "alpha": ("roam.commands.cmd_alpha", "alpha_cmd"),',
        '    "alpha-alias": ("roam.commands.cmd_alpha", "alpha_cmd"),',
    ]
    registry += [f'    "{c}": ("roam.commands.cmd_{c}", "{c}_cmd"),' for c in _OTHER_COMMANDS]
    files = {
        "src/roam/__init__.py": "",
        "src/roam/__main__.py": 'from roam.cli import cli\n\nif __name__ == "__main__":\n    cli()\n',
        "src/roam/commands/__init__.py": "",
        "src/roam/cli.py": (
            "import importlib\n\nimport click\n\n"
            "_COMMANDS = {\n" + "\n".join(registry) + "\n}\n\n\n"
            "def load_command(name):\n"
            "    module_path, attr = dict(_COMMANDS)[name]\n"
            "    return getattr(importlib.import_module(module_path), attr)\n\n\n"
            "class LazyGroup(click.Group):\n"
            "    def get_command(self, ctx, name):\n"
            "        return load_command(name)\n\n\n"
            "@click.group(cls=LazyGroup)\n"
            '@click.option("--json", "json_mode", is_flag=True)\n'
            '@click.option("--select", default=None)\n'
            "def cli(json_mode=False, select=None, name=None):\n"
            "    if name:\n"
            "        load_command(name)\n"
        ),
        "src/roam/commands/cmd_alpha.py": (
            "import click\n\n\n"
            "def helper():\n    return 1\n\n\n"
            '@click.command(name="alpha")\n@click.argument("rest", nargs=-1)\n'
            'def alpha_cmd(rest):\n    click.echo(f"alpha-ran {helper()}")\n'
        ),
        # List-shaped registry iterated by a runner: calling the runner
        # really executes every entry, so traversal must continue here.
        "src/roam/detectors.py": (
            "def det_a():\n    return 1\n\n\n"
            "def det_b():\n    return 2\n\n\n"
            '_DETECTORS = [("a", det_a), ("b", det_b)]\n\n\n'
            "def run_all():\n    entries = list(_DETECTORS)\n    return [fn() for _name, fn in entries]\n"
        ),
        "tests/test_alpha_invoke.py": (
            "import pytest\n"
            "from click.testing import CliRunner\n\n"
            "from roam.cli import cli\n\n\n"
            '@pytest.mark.parametrize("command", ["alpha"])\n'
            "def test_alpha_by_name(command):\n"
            "    args = [command]\n"
            "    result = CliRunner().invoke(cli, args)\n"
            '    assert "alpha-ran" in result.output, result.output\n'
        ),
        "tests/test_alpha_alias.py": (
            "from click.testing import CliRunner\n\n"
            "from roam.cli import cli\n\n\n"
            "def test_alpha_by_alias():\n"
            '    assert "alpha-ran" in CliRunner().invoke(cli, ["alpha-alias"]).output\n'
        ),
        # Imports the dispatcher module, not the name: no literal "roam.cli".
        "tests/test_alpha_module_alias.py": (
            "from click.testing import CliRunner\n\n"
            "from roam import cli\n\n\n"
            "def test_alpha_through_module():\n"
            '    assert "alpha-ran" in CliRunner().invoke(cli.cli, ["alpha"]).output\n'
        ),
        # Iterates the registry: runs every handler and names none of them.
        "tests/test_all_commands.py": (
            "from click.testing import CliRunner\n\n"
            "from roam.cli import _COMMANDS, load_command\n\n\n"
            "def test_every_command_runs():\n"
            "    outputs = []\n"
            "    for name in _COMMANDS:\n"
            "        outputs.append(CliRunner().invoke(load_command(name), []).output)\n"
            '    assert any("alpha-ran" in out for out in outputs)\n'
        ),
        # Dispatches a key computed at run time (the first sorted key, "alpha").
        "tests/test_first_command.py": (
            "from click.testing import CliRunner\n\n"
            "from roam.cli import _COMMANDS, cli\n\n\n"
            "def test_first_command_runs():\n"
            "    name = sorted(_COMMANDS)[0]\n"
            '    assert "alpha-ran" in CliRunner().invoke(cli, [name]).output\n'
        ),
        # Names "alpha" only outside any invocation: runs cmd00, never alpha.
        "tests/test_cmd00_mentions_alpha.py": (
            "from click.testing import CliRunner\n\n"
            "from roam.cli import cli\n\n\n"
            "def test_cmd00_output_is_not_alpha():\n"
            '    # unlike "alpha", cmd00 prints its own name\n'
            '    result = CliRunner().invoke(cli, ["cmd00"])\n'
            '    assert "cmd00_cmd" in result.output and "alpha" not in result.output\n'
        ),
        # The key reaches the dispatcher through a helper's parameters.
        "tests/test_alpha_helper.py": (
            "from click.testing import CliRunner\n\n"
            "from roam.cli import cli\n\n\n"
            "def _invoke(*args):\n    return CliRunner().invoke(cli, list(args))\n\n\n"
            'def test_alpha_via_helper():\n    assert "alpha-ran" in _invoke("alpha").output\n'
        ),
        "tests/test_cmd01_helper.py": (
            "from click.testing import CliRunner\n\n"
            "from roam.cli import cli\n\n\n"
            "def _invoke(*args):\n    return CliRunner().invoke(cli, list(args))\n\n\n"
            'def test_cmd01_via_helper():\n    assert "cmd01_cmd" in _invoke("cmd01").output\n'
        ),
        "tests/test_registry_names.py": (
            'from roam.cli import _COMMANDS\n\n\ndef test_alpha_registered():\n    assert "alpha" in _COMMANDS\n'
        ),
        "tests/test_alpha_patch.py": (
            "from roam.commands import cmd_alpha\n\n\n"
            "def test_patch_helper(monkeypatch):\n"
            '    monkeypatch.setattr(cmd_alpha, "helper", lambda: 2)\n'
        ),
        "tests/test_run_all.py": (
            "from roam.detectors import run_all\n\n\ndef test_run_all():\n    assert run_all() == [1, 2]\n"
        ),
        **_PROPERTY_FILES,
    }
    for c in _OTHER_COMMANDS:
        files[f"src/roam/commands/cmd_{c}.py"] = _command_module(f"{c}_cmd")
        files[f"tests/test_{c}_invoke.py"] = (
            "from click.testing import CliRunner\n\n"
            "from roam.cli import cli, load_command\n\n\n"
            f"def test_{c}():\n"
            f'    assert load_command("{c}")\n'
            f'    result = CliRunner().invoke(cli, ["{c}"])\n'
            f'    assert result.exit_code == 0 and "{c}_cmd" in result.output, result.output\n'
        )
    return files


@pytest.fixture(scope="module")
def registry_project(tmp_path_factory):
    from tests.conftest import git_init, index_in_process

    proj = tmp_path_factory.mktemp("registry_project")
    (proj / ".gitignore").write_text(".roam/\n")
    for rel, content in _files().items():
        path = proj / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    git_init(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, out
    return proj


def _affected(project, monkeypatch, target):
    monkeypatch.chdir(project)
    result = invoke_cli(CliRunner(), ["affected-tests", target], json_mode=True)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _kinds_by_file(data):
    kinds: dict[str, set[str]] = {}
    for entry in data["tests"]:
        kinds.setdefault(entry["file"], set()).add(entry["kind"])
    return kinds


def test_registry_dispatch_is_indexed(registry_project, monkeypatch):
    """Precondition: the fixture really produces the registry hub edge."""
    from roam.db.connection import open_db

    monkeypatch.chdir(registry_project)
    with open_db(readonly=True) as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM edges e JOIN symbols s ON e.source_id = s.id "
            "WHERE e.kind = 'dispatch' AND s.name = '_COMMANDS'"
        ).fetchone()[0]
    assert n == 1 + len(_OTHER_COMMANDS)


def test_test_invoking_registered_name_is_selected(registry_project, monkeypatch):
    kinds = _kinds_by_file(_affected(registry_project, monkeypatch, "alpha_cmd"))
    assert kinds.get("tests/test_alpha_invoke.py") == {"CLI_INVOKE"}


def test_test_importing_target_module_is_selected(registry_project, monkeypatch):
    kinds = _kinds_by_file(_affected(registry_project, monkeypatch, "alpha_cmd"))
    assert kinds.get("tests/test_alpha_patch.py") == {"MODULE_IMPORT"}


_ALPHA_INVOKE = {
    "tests/test_alpha_invoke.py",
    "tests/test_alpha_alias.py",
    "tests/test_alpha_module_alias.py",
    "tests/test_alpha_helper.py",
    "tests/test_p1_literal_and_fixture.py",
    "tests/test_p1_helper_alternatives.py",
    "tests/test_p1_parametrized.py",
    "tests/test_p2_load_and_invoke.py",
    "tests/test_p4_select_inline.py",
    "tests/test_p4_select_stored.py",
    "tests/test_p5_subprocess.py",
    "tests/test_p5_method_local_import.py",
}
_ALPHA_POSSIBLE = {
    "tests/test_first_command.py",
    "tests/test_p1_one_of.py",
    "tests/test_p2_describe.py",
    "tests/test_p2_load_only.py",
    "tests/test_p4_command_then_key_inline.py",
    "tests/test_p4_command_then_key_stored.py",
    "tests/test_p5_unresolved.py",
    "tests/test_p5_runpy.py",
    "tests/test_p5_shell_string.py",  # a shell string is never proof that its command ran
}

# Every fixture test that claims to run a handler: each must pass, and
# each must fail once no click command body runs.
_RUNNERS = (
    *sorted(_ALPHA_INVOKE),
    "tests/test_all_commands.py",
    "tests/test_first_command.py",
    "tests/test_cmd00_mentions_alpha.py",
    "tests/test_cmd01_helper.py",
    "tests/test_p1_one_of.py",
    "tests/test_p4_command_then_key_inline.py",
    "tests/test_p4_command_then_key_stored.py",
    "tests/test_p4_option_value_is_a_key.py",
    "tests/test_p4_helper_argv.py",
    "tests/test_p4_nested_helpers.py",
    "tests/test_p5_helper.py",
    "tests/test_p5_unresolved.py",
    "tests/test_p5_runpy.py",
    "tests/test_p5_shell_string.py",
    *(f"tests/test_{c}_invoke.py" for c in _OTHER_COMMANDS),
)
_RUNNER_CASES = len(_RUNNERS) + 1  # tests/test_p1_parametrized.py runs two cases

# Put on PYTHONPATH for the fixture pytest and every child process it
# starts: with the variable set, no click command body runs.
_SUPPRESS_HANDLERS = (
    "import os\n\n"
    'if os.environ.get("FIXTURE_SUPPRESS_HANDLERS"):\n'
    "    import click\n\n"
    "    click.Command.invoke = lambda self, ctx: None\n"
)


def _run_fixture_tests(project, plugin_dir, suppress):
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(project / "src"), str(plugin_dir)])}
    env.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONDONTWRITEBYTECODE="1")
    env.pop("CI", None)
    env.pop("FIXTURE_SUPPRESS_HANDLERS", None)
    if suppress:
        env["FIXTURE_SUPPRESS_HANDLERS"] = "1"
    # Its own empty config: a temporary directory under a configured tree
    # would otherwise inherit that tree's addopts and plugins.
    config = ["-c", str(plugin_dir / "pytest.ini"), "--rootdir", str(project)]
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *config, *_RUNNERS],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


@pytest.fixture(scope="module")
def suppress_plugin_dir(tmp_path_factory):
    plugin_dir = tmp_path_factory.mktemp("suppress_plugin")
    (plugin_dir / "sitecustomize.py").write_text(_SUPPRESS_HANDLERS)
    (plugin_dir / "pytest.ini").write_text("[pytest]\n")
    return plugin_dir


def test_fixture_tests_reach_the_handler(registry_project, suppress_plugin_dir):
    """Every fixture test that stands for running a handler really runs it.

    The fixture package is named ``roam``, so it runs in its own
    interpreter with only the fixture ``src`` on the path.
    """
    proc = _run_fixture_tests(registry_project, suppress_plugin_dir, suppress=False)
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-2000:]
    assert f"{_RUNNER_CASES} passed" in proc.stdout, proc.stdout[-3000:]


def test_fixture_runners_fail_when_handlers_are_suppressed(registry_project, suppress_plugin_dir):
    """A runner that still passes with no handler body executed proves nothing."""
    proc = _run_fixture_tests(registry_project, suppress_plugin_dir, suppress=True)
    assert f"{_RUNNER_CASES} failed" in proc.stdout, proc.stdout[-3000:]
    assert " passed" not in proc.stdout, proc.stdout[-3000:]


@pytest.mark.parametrize("target", ["alpha_cmd", "src/roam/commands/cmd_alpha.py"])
def test_registry_does_not_fan_out_to_other_commands_tests(registry_project, monkeypatch, target):
    kinds = _kinds_by_file(_affected(registry_project, monkeypatch, target))
    assert not any(f"test_{c}_invoke.py" in f for f in kinds for c in _OTHER_COMMANDS), sorted(kinds)
    assert {f for f, k in kinds.items() if k == {"CLI_INVOKE"}} == _ALPHA_INVOKE
    assert {f for f, k in kinds.items() if k == {"CLI_POSSIBLE"}} == _ALPHA_POSSIBLE
    assert set(kinds) == {*_ALPHA_INVOKE, *_ALPHA_POSSIBLE, "tests/test_all_commands.py", "tests/test_alpha_patch.py"}


@pytest.mark.parametrize("path", ["tests/test_alpha_alias.py", "tests/test_alpha_helper.py"])
def test_alias_key_and_helper_parameter_are_cli_invoke(registry_project, monkeypatch, path):
    kinds = _kinds_by_file(_affected(registry_project, monkeypatch, "alpha_cmd"))
    assert kinds.get(path) == {"CLI_INVOKE"}


def test_module_import_of_dispatcher_is_cli_invoke(registry_project, monkeypatch):
    """``from roam import cli`` + ``invoke(cli.cli, ["alpha"])`` names no ``roam.cli`` text."""
    kinds = _kinds_by_file(_affected(registry_project, monkeypatch, "alpha_cmd"))
    assert kinds.get("tests/test_alpha_module_alias.py") == {"CLI_INVOKE"}


def test_registry_iteration_keeps_the_path(registry_project, monkeypatch):
    """Running every entry of the registry runs every handler: proven, TRANSITIVE."""
    data = _affected(registry_project, monkeypatch, "alpha_cmd")
    path = "tests/test_all_commands.py"
    assert _kinds_by_file(data).get(path) == {"TRANSITIVE"}
    assert {e["via"] for e in data["tests"] if e["file"] == path} == {"_COMMANDS"}


def test_computed_key_is_cli_possible(registry_project, monkeypatch):
    """A key computed at run time may name any handler: selected, not proven."""
    kinds = _kinds_by_file(_affected(registry_project, monkeypatch, "cmd05_cmd"))
    assert kinds.get("tests/test_first_command.py") == {"CLI_POSSIBLE"}


@pytest.mark.parametrize(
    "path", ["tests/test_cmd00_mentions_alpha.py", "tests/test_registry_names.py", "tests/test_cmd01_helper.py"]
)
def test_key_literal_outside_an_invocation_is_not_selected(registry_project, monkeypatch, path):
    kinds = _kinds_by_file(_affected(registry_project, monkeypatch, "alpha_cmd"))
    assert path not in kinds, kinds.get(path)


@pytest.mark.parametrize("command", _OTHER_COMMANDS[:3])
def test_handler_reached_only_through_the_registry_keeps_its_tests(registry_project, monkeypatch, command):
    kinds = _kinds_by_file(_affected(registry_project, monkeypatch, f"{command}_cmd"))
    assert kinds.get(f"tests/test_{command}_invoke.py") == {"CLI_INVOKE"}
    assert kinds.get("tests/test_all_commands.py") == {"TRANSITIVE"}
    assert kinds.get("tests/test_first_command.py") == {"CLI_POSSIBLE"}
    assert "tests/test_alpha_invoke.py" not in kinds


# -- Alternatives are never suppressed -----------------------------------------


def test_literal_beside_an_unresolved_key_keeps_the_path_open(registry_project, monkeypatch):
    """``for key in ["alpha", command]`` also runs whatever ``command`` is."""
    kinds = _kinds_by_file(_affected(registry_project, monkeypatch, "cmd02_cmd"))
    assert kinds.get("tests/test_p1_literal_and_fixture.py") == {"CLI_POSSIBLE"}


def test_literal_beside_a_helper_parameter_keeps_the_parameter(registry_project, monkeypatch):
    """The helper loops over ``["alpha", extra]``: the caller's ``extra`` runs too."""
    kinds = _kinds_by_file(_affected(registry_project, monkeypatch, "cmd03_cmd"))
    assert kinds.get("tests/test_p1_helper_alternatives.py") == {"CLI_INVOKE"}


def test_every_parametrize_case_runs(registry_project, monkeypatch):
    kinds = _kinds_by_file(_affected(registry_project, monkeypatch, "cmd05_cmd"))
    assert kinds.get("tests/test_p1_parametrized.py") == {"CLI_INVOKE"}


def test_one_of_two_keys_is_not_proven(registry_project, monkeypatch):
    """``"alpha" if flag else "cmd04"`` runs one of them: both stay selected, neither proven."""
    for command in ("alpha", "cmd04"):
        kinds = _kinds_by_file(_affected(registry_project, monkeypatch, f"{command}_cmd"))
        assert kinds.get("tests/test_p1_one_of.py") == {"CLI_POSSIBLE"}, command


# -- Reading is not running ----------------------------------------------------


@pytest.mark.parametrize("path", ["tests/test_p2_describe.py", "tests/test_p2_load_only.py"])
def test_registry_read_is_at_most_possible(registry_project, monkeypatch, path):
    kinds = _kinds_by_file(_affected(registry_project, monkeypatch, "alpha_cmd"))
    assert kinds.get(path) == {"CLI_POSSIBLE"}


def test_loaded_entry_that_is_invoked_is_cli_invoke(registry_project, monkeypatch):
    kinds = _kinds_by_file(_affected(registry_project, monkeypatch, "alpha_cmd"))
    assert kinds.get("tests/test_p2_load_and_invoke.py") == {"CLI_INVOKE"}


# -- Argv semantics ------------------------------------------------------------


@pytest.mark.parametrize("path", ["tests/test_p4_select_inline.py", "tests/test_p4_select_stored.py"])
def test_global_option_value_is_not_the_command(registry_project, monkeypatch, path):
    kinds = _kinds_by_file(_affected(registry_project, monkeypatch, "alpha_cmd"))
    assert kinds.get(path) == {"CLI_INVOKE"}


@pytest.mark.parametrize("storage", ["inline", "stored"])
def test_command_position_decides_whatever_the_storage(registry_project, monkeypatch, storage):
    command = {"inline": "cmd04", "stored": "cmd05"}[storage]
    path = f"tests/test_p4_command_then_key_{storage}.py"
    assert _kinds_by_file(_affected(registry_project, monkeypatch, f"{command}_cmd")).get(path) == {"CLI_INVOKE"}
    assert _kinds_by_file(_affected(registry_project, monkeypatch, "alpha_cmd")).get(path) == {"CLI_POSSIBLE"}


@pytest.mark.parametrize(
    ("path", "command", "option_value"),
    [
        ("tests/test_p4_option_value_is_a_key.py", "cmd06", "cmd07"),
        ("tests/test_p4_helper_argv.py", "cmd08", "cmd09"),
        ("tests/test_p5_helper.py", "cmd10", "cmd11"),
    ],
)
def test_key_shaped_option_value_is_consumed(registry_project, monkeypatch, path, command, option_value):
    assert _kinds_by_file(_affected(registry_project, monkeypatch, f"{command}_cmd")).get(path) == {"CLI_INVOKE"}
    assert path not in _kinds_by_file(_affected(registry_project, monkeypatch, f"{option_value}_cmd"))


def test_argv_through_two_helpers_is_cli_invoke(registry_project, monkeypatch):
    kinds = _kinds_by_file(_affected(registry_project, monkeypatch, "cmd11_cmd"))
    assert kinds.get("tests/test_p4_nested_helpers.py") == {"CLI_INVOKE"}


# -- Subprocess invocations ----------------------------------------------------


@pytest.mark.parametrize("path", ["tests/test_p5_subprocess.py", "tests/test_p5_method_local_import.py"])
def test_subprocess_invocation_is_cli_invoke(registry_project, monkeypatch, path):
    kinds = _kinds_by_file(_affected(registry_project, monkeypatch, "alpha_cmd"))
    assert kinds.get(path) == {"CLI_INVOKE"}


def test_shell_string_naming_the_command_is_only_possible(registry_project, monkeypatch):
    """A shell may skip, redefine or quote away the command it names: selected, never proven."""
    kinds = _kinds_by_file(_affected(registry_project, monkeypatch, "alpha_cmd"))
    assert kinds.get("tests/test_p5_shell_string.py") == {"CLI_POSSIBLE"}


def test_other_programs_are_not_selected(registry_project, monkeypatch):
    kinds = _kinds_by_file(_affected(registry_project, monkeypatch, "alpha_cmd"))
    assert "tests/test_p5_not_roam.py" not in kinds


def test_code_that_starts_the_entry_point_is_cli_possible(registry_project, monkeypatch):
    kinds = _kinds_by_file(_affected(registry_project, monkeypatch, "cmd03_cmd"))
    assert kinds.get("tests/test_p5_runpy.py") == {"CLI_POSSIBLE"}


def test_unresolved_subprocess_argv_is_cli_possible(registry_project, monkeypatch):
    kinds = _kinds_by_file(_affected(registry_project, monkeypatch, "cmd03_cmd"))
    assert kinds.get("tests/test_p5_unresolved.py") == {"CLI_POSSIBLE"}


def _word(value):
    return (frozenset({value}), False, False)


@pytest.mark.parametrize(
    ("argv", "selected", "opaque"),
    [
        (["roam", "alpha"], "run", False),
        (["roam.exe", "--json", "alpha"], "run", False),
        (["/usr/local/bin/roam", "--select", ".x", "alpha"], "run", False),
        (["C:\\venv\\Scripts\\roam.exe", "alpha"], "run", False),
        (["python3", "-X", "utf8", "-m", "roam", "alpha"], "run", False),
        (["py", "-m", "roam.__main__", "alpha"], "run", False),
        (["roam", "--select", "alpha", "beta"], None, False),
        (["roam", "beta", "alpha"], "may", False),
        # Allowlisted programs, named bare or under /bin or /usr/bin.
        (["echo", "roam", "alpha"], None, False),
        (["/bin/true"], None, False),
        (["/usr/bin/sleep", "0"], None, False),
        # Everything else may start the package: git and python in any form,
        # and an allowlisted name found elsewhere.
        (["python", "-c", "print('alpha')"], None, True),
        (["python", "-c", "import time; time.sleep(1)"], None, True),
        (["python", "-V"], None, True),
        (["git", "--version"], None, True),
        (["git", "-C", "repo", "commit", "-m", "alpha"], None, True),
        (["git", "config", "--get", "core.hooksPath"], None, True),
        (["git", "status"], None, True),
        (["./echo"], None, True),
        (["bin/echo"], None, True),
        (["/usr/local/bin/echo"], None, True),
        (["/tmp/bin/true"], None, True),
        (["roamx", "alpha"], None, True),
        (["python", "-m", "roamx", "alpha"], None, True),
        (["python", "-c", "import roam"], None, True),
        (["python", "-c", "exec(open('w.py').read())"], None, True),
        (["python", "-c", "help('roam')"], None, True),
        (["python", "-c", "import os; os.system('roam alpha')"], None, True),
        (["python", "-c", "("], None, True),
        (["python"], None, True),
        (["git", "-c", "alias.x=!roam alpha", "x"], None, True),
        (["git", "roam-alpha"], None, True),
        (["git", "-c", "core.hooksPath=hooks", "commit", "-m", "alpha"], None, True),
        (["git", "config", "core.hooksPath", "hooks"], None, True),
        (["git", "config", "set", "alias.x", "!roam alpha"], None, True),
        (["git", "clone", "--upload-pack=roam alpha", "file:///repo"], None, True),
        (["git", "fetch", "ext::roam alpha"], None, True),
        (["git", "remote", "update"], None, True),
        (["git", "--exec-path=bin", "status"], None, True),
        (["env", "roam", "alpha"], None, True),
        (["uv", "--quiet", "run", "roam", "alpha"], None, True),
        (["sh"], None, True),
    ],
)
def test_executable_forms(argv, selected, opaque):
    from roam.commands.cmd_affected_tests import _Reach, _reach_exec

    reach = _Reach()
    _reach_exec(tuple(_word(v) for v in argv), reach, {"roam"}, {"roam", "roam.__main__"}, {"--json": 0, "--select": 1})
    got = "run" if "alpha" in reach.run else "may" if "alpha" in reach.may else None
    assert got == selected, (reach.run, reach.may)
    assert reach.any == opaque


def test_unresolved_program_reads_its_argv_as_a_possible_run():
    """An unresolved program may be the package: its argv is read, never proven."""
    from roam.commands.cmd_affected_tests import _UNKNOWN, _Reach, _reach_exec

    def reach_of(*argv):
        reach = _Reach()
        _reach_exec((_word(_UNKNOWN), *map(_word, argv)), reach, {"roam"}, {"roam"}, {})
        return reach

    possible = reach_of("alpha")
    assert "alpha" in possible.may and not possible.run and possible.any
    assert reach_of(_UNKNOWN).any
    other = reach_of("rev-parse", "HEAD")
    assert other.any and not (other.run or other.every)


def test_command_strings_split_as_one_argv_never_as_a_shell():
    """``shlex.split`` and ``CliRunner.invoke`` strings are one argv: ``;``, ``&&`` and ``>`` are words."""
    from roam.commands.cmd_affected_tests import _split_sequences

    sequences = _split_sequences(["FOO=1 roam --json 'alpha' && git status; echo \";\" > out.txt"])
    assert [[next(iter(v)) for v, _o, _s in seq] for seq in sequences] == [
        ["FOO=1", "roam", "--json", "alpha", "&&", "git", "status;", "echo", ";", ">", "out.txt"],
    ]


def test_iterated_list_registry_still_traverses(registry_project, monkeypatch):
    """Control: a runner that executes every list entry keeps its tests."""
    kinds = _kinds_by_file(_affected(registry_project, monkeypatch, "det_a"))
    assert kinds.get("tests/test_run_all.py") == {"TRANSITIVE"}


def test_preflight_counts_file_level_kinds_as_coverage(registry_project, monkeypatch):
    """A handler covered only by file-level kinds is not 'untested'."""
    monkeypatch.chdir(registry_project)
    result = invoke_cli(CliRunner(), ["preflight", "alpha_cmd"], json_mode=True)
    assert result.exit_code == 0, result.output
    tests = json.loads(result.output)["tests"]
    assert tests["cli_invoke"] == len(_ALPHA_INVOKE)
    assert tests["cli_possible"] == len(_ALPHA_POSSIBLE)
    assert tests["module_import"] == 1
    kinds = ("direct", "transitive", "colocated", "cli_invoke", "module_import", "cli_possible")
    assert sum(tests[k] for k in kinds) == tests["total"]
    assert tests["severity"] == "OK"


def test_preflight_does_not_count_possible_tests_as_coverage():
    from roam.commands.cmd_preflight import _test_severity

    assert _test_severity(0, 0, 0, 0, 0) == "WARNING"
    assert _test_severity(0, 0, 0, 1, 0) == "OK"


def test_count_kinds_sums_to_total():
    from roam.commands.cmd_affected_tests import count_kinds

    kinds = ("DIRECT", "CLI_INVOKE", "CLI_INVOKE", "TRANSITIVE", "MODULE_IMPORT", "CLI_POSSIBLE", "COLOCATED")
    counts = count_kinds([{"kind": k} for k in kinds])
    assert counts == {
        "direct": 1,
        "cli_invoke": 2,
        "transitive": 1,
        "module_import": 1,
        "cli_possible": 1,
        "colocated": 1,
    }
    assert count_kinds([]) == dict.fromkeys(counts, 0)


def test_verify_ranks_cli_invoke_with_direct():
    from roam.commands.cmd_verify import _rank_affected_test_entries

    ranked = _rank_affected_test_entries(
        [
            {"file": "tests/test_c.py", "kind": "COLOCATED", "hops": None},
            {"file": "tests/test_m.py", "kind": "MODULE_IMPORT", "hops": None},
            {"file": "tests/test_i.py", "kind": "CLI_INVOKE", "hops": None},
            {"file": "tests/test_p.py", "kind": "CLI_POSSIBLE", "hops": None},
        ]
    )
    assert {path: priority for priority, _hops, path in ranked} == {
        "tests/test_i.py": 1,
        "tests/test_c.py": 2,
        "tests/test_m.py": 3,
        "tests/test_p.py": 3,
    }


def test_attest_evidence_counts_file_level_kinds(monkeypatch):
    from roam.commands import cmd_attest, cmd_diff

    entries = [
        {"file": "tests/test_i.py", "symbol": None, "kind": "CLI_INVOKE", "hops": None},
        {"file": "tests/test_m.py", "symbol": None, "kind": "MODULE_IMPORT", "hops": None},
        {"file": "tests/test_p.py", "symbol": None, "kind": "CLI_POSSIBLE", "hops": None},
    ]
    monkeypatch.setattr(cmd_diff, "_collect_affected_tests", lambda conn, sym_by_file: (entries, "pytest x"))
    evidence = cmd_attest._collect_affected_tests_evidence(None, {})
    counts = (evidence["cli_invoke"], evidence["module_import"], evidence["cli_possible"])
    assert (evidence["selected"], *counts) == (3, 1, 1, 1)
