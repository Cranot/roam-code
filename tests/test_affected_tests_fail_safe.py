"""Fail-safe affected-tests regressions for launches, environment writes, loaded code and fixtures."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

from tests.conftest import git_init, index_in_process
from tests.test_affected_tests_cli_dispatch import _INVOKE_HEADER, _affected, _files, _kinds_by_file


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    root = tmp_path_factory.mktemp("fail_safe")
    files = _files()
    files["src/roam/cli.py"] = files["src/roam/cli.py"].replace(
        "_COMMANDS = {", '_COMMANDS = {"beta": ("roam.commands.cmd_beta", "beta_cmd"),'
    )
    files["src/roam/commands/cmd_beta.py"] = (
        'import click\n@click.command()\ndef beta_cmd():\n    click.echo("beta-ran")\n'
    )
    files["wrapper.py"] = "from roam.cli import cli\ncli(['alpha'])\n"
    files["wrapper.sh"] = "python wrapper.py\n"
    processes = {
        "python_script": "[sys.executable, 'wrapper.py']",
        "python_path": "[sys.executable, str(Path.cwd() / 'wrapper.py')]",
        "shell_script": "['bash', 'wrapper.sh']",
        "shell_code": "['sh', '-c', 'python wrapper.py']",
        "launcher": "['uv', 'run', 'roam', 'alpha']",
        "pipx": "['pipx', 'run', 'wrapper', 'alpha']",
        "poetry": "['poetry', 'run', 'python', 'wrapper.py']",
        "hatch": "['hatch', 'run', 'wrapper']",
        "module": "[sys.executable, '-m', 'wrapper']",
        "code": "[sys.executable, '-c', \"exec(open('wrapper.py').read())\"]",
    }
    for name, argv in processes.items():
        files[f"tests/test_{name}.py"] = (
            "import subprocess, sys\nfrom pathlib import Path\ndef test_run():\n"
            f"    result = subprocess.run({argv}, capture_output=True, text=True)\n"
            "    assert 'alpha-ran' in result.stdout, result.stderr\n"
        )
    files["tests/test_closure.py"] = _INVOKE_HEADER + (
        "key = 'beta'\ndef test_closure():\n    key = 'alpha'\n"
        "    def inner():\n        return CliRunner().invoke(cli, [key]).output\n"
        "    assert 'alpha-ran' in inner()\n"
    )
    files["tests/test_closure_unknown.py"] = (
        "import os\n"
        + _INVOKE_HEADER
        + (
            "key = 'beta'\ndef test_closure():\n    key = os.environ.get('KEY', 'alpha')\n"
            "    def middle():\n        def inner():\n            return CliRunner().invoke(cli, [key]).output\n"
            "        return inner()\n    assert 'alpha-ran' in middle()\n"
        )
    )
    files["src/roam/dynamic.py"] = (
        "import os\nfrom roam.cli import load_command, _COMMANDS\n"
        "def dynamic():\n    key = os.environ.get('KEY', 'alpha')\n    return load_command(key)\n"
        "def lookup():\n    key = os.environ.get('KEY', 'alpha')\n    return _COMMANDS[key]\n"
    )
    files["src/roam/lookups.py"] = (
        "import os\nfrom roam.cli import _COMMANDS\n"
        "def lookup():\n    key = os.environ.get('KEY', 'alpha')\n    return _COMMANDS[key]\n"
    )
    for name in ("dynamic", "lookup"):
        module = "dynamic" if name == "dynamic" else "lookups"
        files[f"tests/test_{name}.py"] = f"from roam.{module} import {name}\ndef test_run():\n    assert {name}()\n"
    for ending in ("break", "return", "raise RuntimeError('stop')"):
        name = ending.split()[0]
        files[f"tests/test_early_{name}.py"] = _INVOKE_HEADER + (
            "def test_run():\n    for key in ['beta', 'alpha']:\n"
            "        CliRunner().invoke(cli, [key])\n"
            f"        {ending}\n"
        )
    files["tests/test_exit_before.py"] = _INVOKE_HEADER + (
        "def test_run(flag):\n    for key in ['beta', 'alpha']:\n"
        "        if flag:\n            return\n        CliRunner().invoke(cli, [key])\n"
    )
    files["tests/test_closure_argv.py"] = _INVOKE_HEADER + (
        "argv = ['beta']\ndef test_run():\n    argv = ['alpha']\n"
        "    def inner():\n        return CliRunner().invoke(cli, argv).output\n"
        "    assert 'alpha-ran' in inner()\n"
    )
    files["tests/test_single_exit.py"] = _INVOKE_HEADER + (
        "def test_run(flag):\n    for key in ['alpha']:\n"
        "        if flag:\n            return\n        CliRunner().invoke(cli, [key])\n"
    )
    files["tests/test_explicit_global.py"] = _INVOKE_HEADER + (
        "key = 'beta'\ndef test_run():\n    key = 'alpha'\n"
        "    def inner():\n        global key\n        return CliRunner().invoke(cli, [key]).output\n"
        "    assert 'beta-ran' in inner()\n"
    )
    files["tests/test_conditional_first.py"] = _INVOKE_HEADER + (
        "def test_run(flag):\n    for key in ['beta', 'alpha']:\n"
        "        flag and CliRunner().invoke(cli, [key])\n        break\n"
    )
    for name, before, after in (
        ("argv_break", "", "        break\n"),
        ("argv_return", "", "        return\n"),
        ("argv_raise", "", "        raise RuntimeError('stop')\n"),
        ("argv_exit_before", "        if flag:\n            return\n", ""),
        ("argv_all", "", ""),  # CONTROL: every argv runs without an early exit.
    ):
        files[f"tests/test_{name}.py"] = _INVOKE_HEADER + (
            "def test_run(flag):\n    for argv in [['beta'], ['alpha']]:\n"
            + before
            + "        CliRunner().invoke(cli, argv)\n"
            + after
        )
    _allowlist_cases(files)
    _git_python_path_cases(files)
    _shell_path_cases(files)
    _shell_string_env_cases(files)
    _loaded_code_cases(files)
    _fixture_helper_cases(files)
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    (root / "wrapper_exec").chmod(0o755)
    (root / "bin" / "echo").chmod(0o755)
    (root / "bin" / "cat").chmod(0o755)
    git_init(root)
    output, rc = index_in_process(root)
    assert rc == 0, output
    return root


@pytest.mark.parametrize(
    "name",
    [
        "python_script",
        "python_path",
        "shell_script",
        "shell_code",
        "launcher",
        "pipx",
        "poetry",
        "hatch",
        "module",
        "code",
    ],
)
def test_process_uncertainty(project, monkeypatch, name):
    kinds = _kinds_by_file(_affected(project, monkeypatch, "alpha_cmd"))
    assert kinds.get(f"tests/test_{name}.py") == {"CLI_POSSIBLE"}
    assert "tests/test_p5_not_roam.py" not in kinds  # CONTROL: echo


@pytest.mark.parametrize(
    "name,kind", [("closure", "CLI_INVOKE"), ("closure_unknown", "CLI_POSSIBLE"), ("closure_argv", "CLI_INVOKE")]
)
def test_enclosing_scope(project, monkeypatch, name, kind):
    kinds = _kinds_by_file(_affected(project, monkeypatch, "alpha_cmd"))
    assert kinds.get(f"tests/test_{name}.py") == {kind}
    if name == "closure":
        assert f"tests/test_{name}.py" not in _kinds_by_file(_affected(project, monkeypatch, "beta_cmd"))


@pytest.mark.parametrize("name", ["dynamic", "lookup"])
def test_production_unknown(project, monkeypatch, name):
    for target in ("alpha_cmd", "cmd11_cmd"):
        kinds = _kinds_by_file(_affected(project, monkeypatch, target))
        assert kinds.get(f"tests/test_{name}.py") == {"CLI_POSSIBLE"}


@pytest.mark.parametrize("name", ["break", "return", "raise"])
def test_early_exit(project, monkeypatch, name):
    path = f"tests/test_early_{name}.py"
    assert _kinds_by_file(_affected(project, monkeypatch, "alpha_cmd")).get(path) == {"CLI_POSSIBLE"}
    assert _kinds_by_file(_affected(project, monkeypatch, "beta_cmd")).get(path) == {"CLI_INVOKE"}


def test_exit_before_invocation(project, monkeypatch):
    for target in ("alpha_cmd", "beta_cmd"):
        assert _kinds_by_file(_affected(project, monkeypatch, target)).get("tests/test_exit_before.py") == {
            "CLI_POSSIBLE"
        }


@pytest.mark.parametrize(
    "name",
    ["python_script", "python_path", "shell_script", "shell_code", "module", "code", "closure", "closure_unknown"],
)
def test_actual_alpha_output(project, tmp_path, name):
    config = tmp_path / "pytest.ini"
    config.write_text("[pytest]\n")
    env = dict(os.environ, PYTHONPATH=str(project / "src"), PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    env.pop("CI", None)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-c", str(config), "-p", "no:cacheprovider", f"tests/test_{name}.py"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_single_value_can_be_skipped(project, monkeypatch):
    kinds = _kinds_by_file(_affected(project, monkeypatch, "alpha_cmd"))
    assert kinds.get("tests/test_single_exit.py") == {"CLI_POSSIBLE"}


def test_explicit_global_control(project, monkeypatch):
    # CONTROL: the original module-global resolution was correct here.
    assert _kinds_by_file(_affected(project, monkeypatch, "beta_cmd")).get("tests/test_explicit_global.py") == {
        "CLI_INVOKE"
    }
    assert "tests/test_explicit_global.py" not in _kinds_by_file(_affected(project, monkeypatch, "alpha_cmd"))


def test_conditional_first_iteration(project, monkeypatch):
    for target in ("alpha_cmd", "beta_cmd"):
        assert _kinds_by_file(_affected(project, monkeypatch, target)).get("tests/test_conditional_first.py") == {
            "CLI_POSSIBLE"
        }


@pytest.mark.parametrize("name", ["argv_break", "argv_return", "argv_raise", "argv_exit_before", "argv_all"])
def test_argv_loop(project, monkeypatch, name):
    path = f"tests/test_{name}.py"
    alpha = "CLI_INVOKE" if name == "argv_all" else "CLI_POSSIBLE"
    beta = "CLI_POSSIBLE" if name == "argv_exit_before" else "CLI_INVOKE"
    assert _kinds_by_file(_affected(project, monkeypatch, "alpha_cmd")).get(path) == {alpha}
    assert _kinds_by_file(_affected(project, monkeypatch, "beta_cmd")).get(path) == {beta}


# -- Allowlist: only an allowlisted program, or code shown unrelated, is excluded -------------------

_ALLOW_HEADER = (
    "import functools, json, os, subprocess, sys\nfrom pathlib import Path\n\nimport pytest\n" + _INVOKE_HEADER
)
_ALLOW_ROAM = "[sys.executable, '-m', 'roam', 'alpha']"

# Tests that start a process the analysis cannot prove unrelated: CLI_POSSIBLE.
_ALLOW_PROCESSES = {
    "exec_wrapper": "    out = subprocess.run(['./wrapper_exec'], capture_output=True, text=True).stdout\n",
    "alias_local": f"    run = subprocess.run\n    out = run({_ALLOW_ROAM}, capture_output=True, text=True).stdout\n",
    "alias_module": f"    out = RUN({_ALLOW_ROAM}, capture_output=True, text=True).stdout\n",
    "partial": (
        "    run = functools.partial(subprocess.run, capture_output=True, text=True)\n"
        f"    out = run({_ALLOW_ROAM}).stdout\n"
    ),
    "lambda": (
        f"    go = lambda: subprocess.run({_ALLOW_ROAM}, capture_output=True, text=True)\n    out = go().stdout\n"
    ),
    "callback": (
        "    outs = []\n    def inner():\n"
        f"        outs.append(subprocess.run({_ALLOW_ROAM}, capture_output=True, text=True).stdout)\n"
        "    for callback in [inner]:\n        callback()\n    out = outs[0]\n"
    ),
    "stdin_shell": (
        "    code = f'\"{sys.executable}\" wrapper.py'\n"
        "    out = subprocess.run(['sh'], input=code, capture_output=True, text=True).stdout\n"
    ),
    "stdin_python": (
        "    code = \"from roam.cli import cli; cli(['alpha'])\"\n"
        "    out = subprocess.run([sys.executable], input=code, capture_output=True, text=True).stdout\n"
    ),
    "env_python": (
        "    out = subprocess.run(['env', sys.executable, 'wrapper.py'], capture_output=True, text=True).stdout\n"
    ),
    # Not executed here (the program is not installed); each runs ``roam alpha`` where it is.
    "env_roam": "    out = subprocess.run(['env', 'roam', 'alpha'], capture_output=True, text=True).stdout\n",
    "uvx": "    out = subprocess.run(['uvx', 'roam', 'alpha'], capture_output=True, text=True).stdout\n",
    "timeout": "    out = subprocess.run(['timeout', '60', 'roam', 'alpha'], capture_output=True, text=True).stdout\n",
    "pdm": "    out = subprocess.run(['pdm', 'run', 'roam', 'alpha'], capture_output=True, text=True).stdout\n",
    "nohup": "    out = subprocess.run(['nohup', 'roam', 'alpha'], capture_output=True, text=True).stdout\n",
    "uv_option": "    out = subprocess.run(['uv', '--quiet', 'run', 'roam', 'alpha'], text=True).stdout\n",
    "shell_timeout": "    out = subprocess.run('timeout 60 roam alpha', shell=True, text=True).stdout\n",
    "git_alias": "    out = subprocess.run(['git', '-c', 'alias.x=!roam alpha', 'x'], text=True).stdout\n",
    "git_hooks": (
        "    subprocess.run(['git', 'config', 'core.hooksPath', 'hooks'])\n"
        "    out = subprocess.run(['git', 'commit', '-m', 'x'], text=True).stdout\n"
    ),
    "git_external": "    out = subprocess.run(['git', 'roam-alpha'], text=True).stdout\n",
    "spawn": "    os.spawnv(os.P_WAIT, sys.executable, [sys.executable, 'wrapper.py'])\n    out = ''\n",
    # The launcher passed as a value; a lambda that calls a launching helper.
    "launcher_passed": (
        "    def call(launch, argv):\n        return launch(argv, capture_output=True, text=True)\n"
        f"    out = call(subprocess.run, {_ALLOW_ROAM}).stdout\n"
    ),
    "lambda_helper": (
        f"    def roam_alpha():\n        return subprocess.run({_ALLOW_ROAM}, capture_output=True, text=True)\n"
        "    go = lambda: roam_alpha()\n    out = go().stdout\n"
    ),
    # An alias another test module binds, and a launch at import time.
    "alias_imported": f"    out = LAUNCH({_ALLOW_ROAM}, capture_output=True, text=True).stdout\n",
    "module_level": "    out = OUT\n",
    "code_import": "    out = subprocess.run([sys.executable, '-c', 'import roam.cli'], text=True).stdout\n",
    "code_dunder": "    out = subprocess.run([sys.executable, '-c', \"__import__('roam.cli')\"], text=True).stdout\n",
    "code_help": "    out = subprocess.run([sys.executable, '-c', \"help('roam')\"], text=True).stdout\n",
}
_ALLOW_EXECUTED = [
    "exec_wrapper",
    "alias_local",
    "alias_module",
    "partial",
    "lambda",
    "callback",
    "stdin_shell",
    "stdin_python",
    "env_python",
    "module_level",
    "launcher_passed",
    "lambda_helper",
]

# Scope and loop shapes: ``{name: (source, alpha kind, beta kind)}``, None for excluded.
_ALLOW_FLOWS = {
    # A nested scope rebinds the name, so the enclosing value proves nothing.
    "nonlocal": (
        "def test_run():\n    key = 'beta'\n    def set_():\n        nonlocal key\n        key = 'alpha'\n"
        "    def inner():\n        return CliRunner().invoke(cli, [key]).output\n"
        "    set_()\n    assert 'alpha-ran' in inner()\n",
        "CLI_POSSIBLE",
        "CLI_POSSIBLE",
    ),
    "nonlocal_direct": (
        "def test_run():\n    key = 'beta'\n    def set_():\n        nonlocal key\n        key = 'alpha'\n"
        "    set_()\n    assert 'alpha-ran' in CliRunner().invoke(cli, [key]).output\n",
        "CLI_POSSIBLE",
        "CLI_POSSIBLE",
    ),
    "global_write": (
        "KEY = 'beta'\ndef set_():\n    global KEY\n    KEY = 'alpha'\n"
        "def test_run():\n    set_()\n    assert 'alpha-ran' in CliRunner().invoke(cli, [KEY]).output\n",
        "CLI_POSSIBLE",
        "CLI_POSSIBLE",
    ),
    # A captured helper parameter is unknown in the inner function.
    "captured_param": (
        "def helper(key):\n    def inner():\n        return CliRunner().invoke(cli, [key]).output\n"
        "    return inner()\n"
        "def test_run():\n    assert 'alpha-ran' in helper('alpha')\n",
        "CLI_POSSIBLE",
        "CLI_POSSIBLE",
    ),
    # After the first value, a loop that may stop early proves nothing.
    **{
        f"loop_{name}": (
            "def _stop():\n    pytest.skip('stop')\n"
            "def test_run():\n    for key in ['beta', 'alpha']:\n"
            f"        CliRunner().invoke(cli, [key])\n        {ending}\n",
            "CLI_POSSIBLE",
            "CLI_INVOKE",
        )
        for name, ending in (
            ("skip", "pytest.skip('stop')"),
            ("fail", "pytest.fail('stop')"),
            ("xfail", "pytest.xfail('stop')"),
            ("sys_exit", "sys.exit(0)"),
            ("os_exit", "os._exit(0)"),
            ("helper", "_stop()"),
        )
    },
    "loop_continue": (
        "def test_run(flag):\n    for key in ['beta', 'alpha']:\n"
        "        if flag:\n            continue\n        CliRunner().invoke(cli, [key])\n",
        "CLI_POSSIBLE",
        "CLI_POSSIBLE",
    ),
    "loop_caught": (
        "def test_run():\n    try:\n        for key in ['beta', 'alpha']:\n"
        "            CliRunner().invoke(cli, [key])\n            json.loads('{')\n"
        "    except ValueError:\n        pass\n",
        "CLI_POSSIBLE",
        "CLI_POSSIBLE",
    ),
    # A site inside a nested loop is not certain in the first iteration.
    "loop_nested": (
        "def test_run(flag):\n    for key in ['beta', 'alpha']:\n"
        "        while flag:\n            CliRunner().invoke(cli, [key])\n            flag = False\n        break\n",
        "CLI_POSSIBLE",
        "CLI_POSSIBLE",
    ),
    # CONTROL: nothing may stop the loop, so every value runs.
    "loop_plain": (
        "def test_run():\n    for key in ['beta', 'alpha']:\n        CliRunner().invoke(cli, [key])\n"
        "        json.dumps(key)\n",
        "CLI_INVOKE",
        "CLI_INVOKE",
    ),
}
_ALLOW_FLOWS_EXECUTED = ["nonlocal", "nonlocal_direct", "global_write", "captured_param"]

# CONTROL: every process here is provably unrelated to the package.
_ALLOW_UNRELATED = (
    "def test_run():\n"
    "    for argv in (['true'], ['ls'], ['cat', 'wrapper.py'], ['sleep', '0'], ['echo', 'roam', 'alpha']):\n"
    "        subprocess.run(argv, check=True, capture_output=True)\n"
)


def _allowlist_cases(files):
    files["wrapper_exec"] = f"#!{sys.executable}\nfrom roam.cli import cli\ncli(['alpha'])\n"
    for name, body in _ALLOW_PROCESSES.items():
        module = {
            "alias_module": "RUN = subprocess.run\n",
            "alias_imported": "from tests.r4_launch import LAUNCH\n",
            "module_level": f"OUT = subprocess.run({_ALLOW_ROAM}, capture_output=True, text=True).stdout\n",
        }.get(name, "")
        files[f"tests/test_r4_{name}.py"] = (
            _ALLOW_HEADER + module + "def test_run():\n" + body + "    assert 'alpha-ran' in out\n"
        )
    for name, (source, _alpha, _beta) in _ALLOW_FLOWS.items():
        files[f"tests/test_r4_{name}.py"] = _ALLOW_HEADER + source
    files["tests/r4_launch.py"] = "import subprocess\nLAUNCH = subprocess.run\n"
    files["tests/test_r4_unrelated.py"] = _ALLOW_HEADER + _ALLOW_UNRELATED
    files["src/roam/getter.py"] = (
        "import os\nfrom roam.cli import _COMMANDS\n"
        "def lookup():\n    key = os.environ.get('KEY', 'alpha')\n    return _COMMANDS.get(key)\n"
    )
    files["tests/test_r4_getter.py"] = "from roam.getter import lookup\ndef test_run():\n    assert lookup()\n"


@pytest.mark.parametrize("name", sorted(_ALLOW_PROCESSES))
def test_unproven_process_is_possible(project, monkeypatch, name):
    kinds = _kinds_by_file(_affected(project, monkeypatch, "alpha_cmd"))
    # Import-time code always runs: its literal ``python -m roam alpha`` is a proven run.
    expected = "CLI_INVOKE" if name == "module_level" else "CLI_POSSIBLE"
    assert kinds.get(f"tests/test_r4_{name}.py") == {expected}


@pytest.mark.parametrize("target", ["alpha_cmd", "beta_cmd"])
def test_allowlisted_programs_stay_excluded(project, monkeypatch, target):
    kinds = _kinds_by_file(_affected(project, monkeypatch, target))
    assert "tests/test_r4_unrelated.py" not in kinds
    assert "tests/test_p5_not_roam.py" not in kinds


@pytest.mark.parametrize("name", sorted(_ALLOW_FLOWS))
def test_scopes_and_loops(project, monkeypatch, name):
    _source, alpha, beta = _ALLOW_FLOWS[name]
    path = f"tests/test_r4_{name}.py"
    for target, expected in (("alpha_cmd", alpha), ("beta_cmd", beta)):
        got = _kinds_by_file(_affected(project, monkeypatch, target)).get(path)
        assert got == ({expected} if expected else None), (target, got)


def test_get_dispatcher_keeps_possible_reach(project, monkeypatch):
    for target in ("alpha_cmd", "cmd11_cmd"):
        assert _kinds_by_file(_affected(project, monkeypatch, target)).get("tests/test_r4_getter.py") == {
            "CLI_POSSIBLE"
        }


# -- git and python leave the allowlist; a PATH the test controls proves nothing ----------------------

_GITPY_ECHO = "str(Path.cwd() / 'bin' / 'echo')"
_GITPY_BUILTINS = (
    "from json import __builtins__ as b; b['__import__']('sys').path.insert(0, 'src'); "
    "b['__import__']('roam.cli', fromlist=['cli']).cli(['alpha'])"
)
_GITPY_RUN = "capture_output=True, text=True).stdout\n"
# Tests that start git, python, or an allowlisted name found through a PATH they
# control: CLI_POSSIBLE.  Each executed one reaches alpha through bin/echo.
_GITPY_PROCESSES = {
    # Five launches that reach alpha.
    "git_editor": f"    out = subprocess.run(['git', 'config', '--edit'], env=dict(os.environ, GIT_EDITOR={_GITPY_ECHO}), "
    + _GITPY_RUN,
    "git_external_diff": (
        "    Path('tracked').write_text('changed')\n"
        f"    out = subprocess.run(['git', 'diff', '--ext-diff'], env=dict(os.environ, GIT_EXTERNAL_DIFF={_GITPY_ECHO}), "
        + _GITPY_RUN
        + "    Path('tracked').write_text('original')\n"
    ),
    "git_config_env": (
        "    env = dict(os.environ, GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0='core.fsmonitor', "
        f"GIT_CONFIG_VALUE_0={_GITPY_ECHO})\n"
        "    out = subprocess.run(['git', 'status'], env=env, " + _GITPY_RUN
    ),
    "python_builtins": f"    out = subprocess.run([sys.executable, '-c', {_GITPY_BUILTINS!r}], " + _GITPY_RUN,
    "path_echo": "    out = subprocess.run(['echo'], env=dict(os.environ, PATH=str(Path.cwd() / 'bin')), " + _GITPY_RUN,
    # Every way a test can set PATH for the name it launches.
    "path_environ": (
        "    os.environ['PATH'] = str(Path.cwd() / 'bin') + os.pathsep + os.environ['PATH']\n"
        "    out = subprocess.run(['echo'], " + _GITPY_RUN
    ),
    "path_update": "    os.environ.update(PATH=str(Path.cwd() / 'bin'))\n    out = subprocess.run(['echo'], "
    + _GITPY_RUN,
    "path_key": (
        "    key = 'PA' + 'TH'\n    os.environ[key] = str(Path.cwd() / 'bin')\n"
        "    out = subprocess.run(['echo'], " + _GITPY_RUN
    ),
    "path_setenv": (
        "    monkeypatch.setenv('PATH', str(Path.cwd() / 'bin'))\n    out = subprocess.run(['echo'], " + _GITPY_RUN
    ),
    "path_helper": "    monkeypatch.setenv('PATH', str(Path.cwd() / 'bin'))\n    out = r5_echo()\n",
    "executable": f"    out = subprocess.run(['echo'], executable={_GITPY_ECHO}, " + _GITPY_RUN,
    "relative": "    out = subprocess.run(['./bin/echo'], " + _GITPY_RUN,
    # Not executed here: each starts a program the allowlist no longer proves.
    "git_status": "    out = subprocess.run(['git', 'status'], " + _GITPY_RUN,
    "python_code": "    out = subprocess.run([sys.executable, '-c', 'print(1)'], " + _GITPY_RUN,
    "python_version": "    out = subprocess.run([sys.executable, '-V'], " + _GITPY_RUN,
    "local_bin": "    out = subprocess.run(['/usr/local/bin/echo'], " + _GITPY_RUN,
    "path_delenv": "    monkeypatch.delenv('PATH')\n    out = subprocess.run(['echo'], " + _GITPY_RUN,
    "path_putenv": "    os.putenv('PATH', 'bin')\n    out = subprocess.run(['echo'], " + _GITPY_RUN,
    "path_patch_dict": (
        "    from unittest import mock\n    with mock.patch.dict(os.environ, {'PATH': 'bin'}):\n"
        "        out = subprocess.run(['echo'], " + _GITPY_RUN
    ),
    "env_kwargs": "    kw = {'env': None}\n    out = subprocess.run(['echo'], **kw, " + _GITPY_RUN,
    "conftest": "    out = subprocess.run(['echo'], " + _GITPY_RUN,
}
_GITPY_EXECUTED = [
    "git_editor",
    "git_external_diff",
    "git_config_env",
    "python_builtins",
    "path_echo",
    "path_environ",
    "path_update",
    "path_key",
    "path_setenv",
    "path_helper",
    "executable",
    "relative",
    "conftest",
]
# CONTROLS: allowlisted names found through a PATH the test leaves alone.
_GITPY_CONTROL_SOURCES = {
    "plain": (
        "    subprocess.run(['echo', 'hi'], check=True)\n    subprocess.run(['sleep', '0'], check=True)\n"
        "    subprocess.run(['/bin/echo', 'hi'], check=True)\n    subprocess.run(['/usr/bin/true'], check=True)\n"
    ),
}
_GITPY_CONTROLS = [f"control_{name}" for name in _GITPY_CONTROL_SOURCES]
_GITPY_PATHS = {
    **{name: f"tests/test_r5_{name}.py" for name in (*_GITPY_PROCESSES, *_GITPY_CONTROLS)},
    "conftest": "tests/r5_env/test_r5_conftest.py",
}
_GITPY_WRAPPER = (
    f"#!{sys.executable}\nimport sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path.cwd() / 'src'))\n"
    "from roam.cli import cli\nPath('ran_alpha').write_text('yes')\ncli(['alpha'])\n"
)


def _git_python_path_cases(files):
    files["bin/echo"] = _GITPY_WRAPPER
    files["tracked"] = "original"
    files["tests/r5_echo.py"] = "import subprocess\ndef r5_echo():\n    return subprocess.run(['echo'], " + _GITPY_RUN
    files["tests/r5_env/conftest.py"] = (
        "from pathlib import Path\nimport pytest\n@pytest.fixture(autouse=True)\n"
        "def _path(monkeypatch):\n    monkeypatch.setenv('PATH', str(Path.cwd() / 'bin'))\n"
    )
    for name, body in _GITPY_PROCESSES.items():
        files[_GITPY_PATHS[name]] = (
            _ALLOW_HEADER + "from tests.r5_echo import r5_echo\n\n"
            "def test_run(monkeypatch):\n    marker = Path('ran_alpha')\n    marker.unlink(missing_ok=True)\n"
            + body
            + "    assert 'alpha-ran' in out or marker.exists(), out\n"
        )
    for name, body in _GITPY_CONTROL_SOURCES.items():
        files[_GITPY_PATHS[f"control_{name}"]] = _ALLOW_HEADER + "def test_run(monkeypatch):\n" + body


@pytest.mark.parametrize("name", sorted(_GITPY_PROCESSES))
def test_unproven_program_or_environment_is_possible(project, monkeypatch, name):
    for target in ("alpha_cmd", "beta_cmd"):
        assert _kinds_by_file(_affected(project, monkeypatch, target)).get(_GITPY_PATHS[name]) == {"CLI_POSSIBLE"}


@pytest.mark.parametrize("target", ["alpha_cmd", "beta_cmd"])
def test_allowlisted_names_in_an_unchanged_environment_stay_excluded(project, monkeypatch, target):
    kinds = _kinds_by_file(_affected(project, monkeypatch, target))
    assert not {_GITPY_PATHS[name] for name in _GITPY_CONTROLS} & set(kinds)
    assert "tests/r5_echo.py" not in kinds


# -- Shell launches: a shell may run anything; PATH is found through any environment spelling --------

# Module-level imports that bind the environment, its setters, or a patcher under other names.
_SHELLPATH_IMPORTS = (
    "import os as operating\nimport os.path\nfrom os import environ as environment\n"
    "from os import putenv as put\nfrom unittest.mock import patch as patcher\n"
)
_SHELLPATH_BIN = "str(Path.cwd() / 'bin') + os.pathsep + os.environ['PATH']"
# Tests whose launch the analysis cannot prove unrelated: CLI_POSSIBLE.
_SHELLPATH_PROCESSES = {
    # Four launches that reach alpha; bin/cat, as bin/echo, runs alpha (echo is a shell builtin).
    "shell_path": "    out = subprocess.run('PATH=bin cat', shell=True, " + _GITPY_RUN,
    "shell_substitution": "    out = subprocess.run('echo \"$(python3 wrapper.py)\"', shell=True, " + _GITPY_RUN,
    "alias_os": f"    operating.environ['PATH'] = {_SHELLPATH_BIN}\n    out = subprocess.run(['echo'], " + _GITPY_RUN,
    "alias_environ": f"    environment['PATH'] = {_SHELLPATH_BIN}\n    out = subprocess.run(['echo'], " + _GITPY_RUN,
    # Every other launch through a shell, and every other spelling of a PATH change.
    "shell_os_system": "    os.system('PATH=bin cat > out.txt')\n    out = Path('out.txt').read_text()\n",
    "shell_popen": "    out = os.popen('PATH=bin cat').read()\n",
    "shell_getoutput": "    out = subprocess.getoutput('PATH=bin cat')\n",
    "shell_plain": "    out = subprocess.run('echo roam alpha > out.txt 2>/dev/null', shell=True, " + _GITPY_RUN,
    "shell_flag": "    flag = True\n    out = subprocess.run('PATH=bin cat', shell=flag, " + _GITPY_RUN,
    "import_os_path": f"    os.environ['PATH'] = {_SHELLPATH_BIN}\n    out = subprocess.run(['echo'], " + _GITPY_RUN,
    "alias_setdefault": (
        "    environment.pop('PATH')\n    environment.setdefault('PATH', 'bin')\n"
        "    out = subprocess.run(['echo'], " + _GITPY_RUN
    ),
    "alias_update": "    environment.update({'PATH': "
    + _SHELLPATH_BIN
    + "})\n    out = subprocess.run(['echo'], "
    + _GITPY_RUN,
    "alias_del": "    del environment['PATH']\n    out = subprocess.run(['echo'], " + _GITPY_RUN,
    "alias_putenv": "    put('PATH', 'bin')\n    out = subprocess.run(['echo'], " + _GITPY_RUN,
    # A key the analysis cannot read: only the resolved alias shows the environment.
    "alias_os_key": (
        f"    key = 'PA' + 'TH'\n    operating.environ[key] = {_SHELLPATH_BIN}\n    out = subprocess.run(['echo'], "
        + _GITPY_RUN
    ),
    "alias_environ_key": (
        f"    key = 'PA' + 'TH'\n    environment[key] = {_SHELLPATH_BIN}\n    out = subprocess.run(['echo'], "
        + _GITPY_RUN
    ),
    "alias_putenv_key": "    key = 'PA' + 'TH'\n    put(key, 'bin')\n    out = subprocess.run(['echo'], " + _GITPY_RUN,
    "alias_setenv": (
        f"    mp = monkeypatch\n    setenv = mp.setenv\n    setenv('PATH', {_SHELLPATH_BIN})\n"
        "    out = subprocess.run(['echo'], " + _GITPY_RUN
    ),
    "alias_patch_dict": (
        f"    with patcher.dict(environment, {{'PATH': {_SHELLPATH_BIN}}}):\n        out = subprocess.run(['echo'], "
        + _GITPY_RUN
    ),
    "patch_dict_string": (
        f"    with patcher.dict('os.environ', {{'PATH': {_SHELLPATH_BIN}}}):\n        out = subprocess.run(['echo'], "
        + _GITPY_RUN
    ),
    "setitem_string": (
        f"    monkeypatch.setitem(getattr(operating, 'environ'), 'PATH', {_SHELLPATH_BIN})\n"
        "    out = subprocess.run(['echo'], " + _GITPY_RUN
    ),
    "unresolved_key": (
        "    target = next(v for v in vars(operating).values() if isinstance(v, os._Environ))\n"
        f"    target['PATH'] = {_SHELLPATH_BIN}\n"
        "    out = subprocess.run(['echo'], " + _GITPY_RUN
    ),
}
_SHELLPATH_EXECUTED = [
    name
    for name in _SHELLPATH_PROCESSES
    if name
    not in ("shell_plain", "alias_putenv", "alias_putenv_key", "alias_setdefault", "alias_del", "shell_os_system")
]
# CONTROLS: an environment copied, or a dict resolved as not the environment, with a PATH key.
_SHELLPATH_CONTROL_SOURCES = {
    "copied": (
        "    env = dict(os.environ)\n    env['R6_FOO'] = '1'\n    copied = operating.environ.copy()\n"
        "    copied['PATH'] = 'bin'\n    assert env and copied and environment.get('PATH')\n"
        "    subprocess.run(['echo', 'hi'], check=True)\n"
    ),
    "unrelated_dict": (
        "    settings = {}\n    settings['PATH'] = 'bin'\n    settings.update(PATH='bin')\n"
        "    settings.setdefault('PATH', 'bin')\n    literal = {'PATH': 'bin'}\n    literal.pop('PATH')\n"
        "    subprocess.run(['echo', 'hi'], check=True)\n    subprocess.run(['sleep', '0'], check=True)\n"
        "    subprocess.run(['/bin/echo', 'hi'], check=True)\n"
    ),
}
_SHELLPATH_CONTROLS = [f"r6_control_{name}" for name in _SHELLPATH_CONTROL_SOURCES]
_SHELLPATH_PATHS = {
    **{name: f"tests/test_r6_{name}.py" for name in _SHELLPATH_PROCESSES},
    **{f"r6_control_{name}": f"tests/test_r6_control_{name}.py" for name in _SHELLPATH_CONTROL_SOURCES},
}


def _shell_path_cases(files):
    files["bin/cat"] = _GITPY_WRAPPER
    for name, body in _SHELLPATH_PROCESSES.items():
        files[_SHELLPATH_PATHS[name]] = (
            _ALLOW_HEADER + _SHELLPATH_IMPORTS + "\n"
            "def test_run(monkeypatch):\n    marker = Path('ran_alpha')\n    marker.unlink(missing_ok=True)\n"
            + body
            + "    assert 'alpha-ran' in out or marker.exists(), out\n"
        )
    for name, body in _SHELLPATH_CONTROL_SOURCES.items():
        files[_SHELLPATH_PATHS[f"r6_control_{name}"]] = (
            _ALLOW_HEADER + _SHELLPATH_IMPORTS + "def test_run(monkeypatch):\n" + body
        )


@pytest.mark.parametrize("name", sorted(_SHELLPATH_PROCESSES))
def test_shell_or_aliased_path_change_is_possible(project, monkeypatch, name):
    for target in ("alpha_cmd", "beta_cmd"):
        assert _kinds_by_file(_affected(project, monkeypatch, target)).get(_SHELLPATH_PATHS[name]) == {"CLI_POSSIBLE"}


@pytest.mark.parametrize("target", ["alpha_cmd", "beta_cmd"])
def test_copies_and_unrelated_dicts_stay_excluded(project, monkeypatch, target):
    kinds = _kinds_by_file(_affected(project, monkeypatch, target))
    assert not {_SHELLPATH_PATHS[name] for name in _SHELLPATH_CONTROLS} & set(kinds)


# -- Shell strings prove nothing; any environment write voids the allowlist --------------------------

# Shell strings that name ``roam alpha`` but never run it: neither proven nor excluded.
_ENVWRITE_SHELLS = {
    "false_and": "false && roam alpha",
    "unused_function": "f() { :; roam alpha; }; true",
    "quoted_semicolon": 'echo ";" roam alpha',
}
# A write to any key of the environment: an allowlisted program may load code it names (LD_PRELOAD).
_ENVWRITE_ENV = {
    "preload": "    os.environ['LD_PRELOAD'] = str(Path.cwd() / 'preload.so')\n",
    "flag": "    os.environ['ROAM_TEST_FLAG'] = '1'\n",
    "other_env": (
        "    monkeypatch.setenv('R5_FOO', '1')\n    os.environ['R5_BAR'] = os.environ.get('HOME', '')\n"
        "    os.environ.pop('R5_BAR')\n    copied = {**os.environ, 'R5_BAZ': '1'}\n"
    ),
    "setdefault": "    os.environ.setdefault('ROAM_TEST_FLAG', '1')\n",
    "update": "    os.environ.update(ROAM_TEST_FLAG='1')\n",
    "pop": "    os.environ.pop('ROAM_TEST_FLAG', None)\n",
    "del": "    os.environ['ROAM_TEST_FLAG'] = '1'\n    del os.environ['ROAM_TEST_FLAG']\n",
    "putenv": "    os.putenv('ROAM_TEST_FLAG', '1')\n",
    "unsetenv": "    os.unsetenv('ROAM_TEST_FLAG')\n",
    "setenv": "    monkeypatch.setenv('ROAM_TEST_FLAG', '1')\n",
    "delenv": "    monkeypatch.delenv('ROAM_TEST_FLAG', raising=False)\n",
    "patch_dict": "    from unittest import mock\n    mock.patch.dict(os.environ, {'ROAM_TEST_FLAG': '1'}).start()\n",
    "alias": "    from os import environ as environment\n    environment['ROAM_TEST_FLAG'] = '1'\n",
    "unresolved": (
        "    target = next(v for v in vars(os).values() if isinstance(v, os._Environ))\n"
        "    target['ROAM_TEST_FLAG'] = '1'\n"
    ),
}
# CONTROLS: allowlisted programs, and an environment copied but never written.
_ENVWRITE_CONTROL_SOURCES = {
    "plain": (
        "    subprocess.run(['echo', 'hi'], check=True)\n    subprocess.run(['sleep', '0'], check=True)\n"
        "    subprocess.run(['/bin/echo', 'hi'], check=True)\n"
    ),
    "copied": (
        "    env = dict(os.environ)\n    merged = {**os.environ, 'ROAM_TEST_FLAG': '1'}\n"
        "    other = os.environ.copy()\n    other['ROAM_TEST_FLAG'] = '1'\n"
        "    assert env and merged and other and 'PATH' in os.environ\n"
        "    subprocess.run(['echo', 'hi'], check=True)\n"
    ),
}
_ENVWRITE_CONTROLS = [f"r7_control_{name}" for name in _ENVWRITE_CONTROL_SOURCES]
_ENVWRITE_PATHS = {
    **{f"shell_{name}": f"tests/test_r7_shell_{name}.py" for name in _ENVWRITE_SHELLS},
    **{f"env_{name}": f"tests/test_r7_env_{name}.py" for name in _ENVWRITE_ENV},
    **{f"r7_control_{name}": f"tests/test_r7_control_{name}.py" for name in _ENVWRITE_CONTROL_SOURCES},
}
_ENVWRITE_PRELOAD = (
    "#include <stdlib.h>\n#include <unistd.h>\n__attribute__((constructor)) static void run(void) "
    f'{{ unsetenv("LD_PRELOAD"); execl("{sys.executable}", "python3", "bin/echo", (char *)0); }}\n'
)


def _shell_string_env_cases(files):
    files["preload.c"] = _ENVWRITE_PRELOAD
    for name, command in _ENVWRITE_SHELLS.items():
        files[_ENVWRITE_PATHS[f"shell_{name}"]] = (
            _ALLOW_HEADER + "def test_run():\n    marker = Path('ran_alpha')\n    marker.unlink(missing_ok=True)\n"
            f"    subprocess.run({command!r}, shell=True, capture_output=True, text=True)\n"
            "    assert not marker.exists()\n"
        )
    for name, body in _ENVWRITE_ENV.items():
        files[_ENVWRITE_PATHS[f"env_{name}"]] = (
            _ALLOW_HEADER + "def test_run(monkeypatch):\n" + body + "    subprocess.run(['/bin/true'], check=True)\n"
            "    subprocess.run(['echo', 'hi'], check=True)\n"
        )
    for name, body in _ENVWRITE_CONTROL_SOURCES.items():
        files[_ENVWRITE_PATHS[f"r7_control_{name}"]] = _ALLOW_HEADER + "def test_run(monkeypatch):\n" + body


@pytest.mark.parametrize("name", sorted(_ENVWRITE_SHELLS))
def test_a_shell_string_is_possible_never_proven(project, monkeypatch, name):
    for target in ("alpha_cmd", "beta_cmd"):
        assert _kinds_by_file(_affected(project, monkeypatch, target)).get(_ENVWRITE_PATHS[f"shell_{name}"]) == {
            "CLI_POSSIBLE"
        }


@pytest.mark.parametrize("name", sorted(_ENVWRITE_ENV))
def test_any_environment_write_voids_the_allowlist(project, monkeypatch, name):
    for target in ("alpha_cmd", "beta_cmd"):
        assert _kinds_by_file(_affected(project, monkeypatch, target)).get(_ENVWRITE_PATHS[f"env_{name}"]) == {
            "CLI_POSSIBLE"
        }


@pytest.mark.parametrize("target", ["alpha_cmd", "beta_cmd"])
def test_allowlisted_programs_and_copied_environments_stay_excluded(project, monkeypatch, target):
    kinds = _kinds_by_file(_affected(project, monkeypatch, target))
    assert not {_ENVWRITE_PATHS[name] for name in _ENVWRITE_CONTROLS} & set(kinds)


@pytest.mark.skipif(sys.platform != "linux" or not shutil.which("cc"), reason="needs a C compiler and ld.so")
def test_preload_fixture_really_runs_alpha(project, tmp_path):
    """/bin/true under a test-set LD_PRELOAD runs alpha: the allowlist cannot exclude it."""
    subprocess.run(["cc", "-shared", "-fPIC", "-o", "preload.so", "preload.c"], cwd=project, check=True, timeout=60)
    marker = project / "ran_alpha"
    marker.unlink(missing_ok=True)
    config = tmp_path / "pytest.ini"
    config.write_text("[pytest]\n")
    env = dict(os.environ, PYTHONPATH=str(project / "src"), PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-c", str(config), "-p", "no:cacheprovider", _ENVWRITE_PATHS["env_preload"]],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0 and marker.exists(), proc.stdout + proc.stderr
    marker.unlink()


# -- Loaded code: code the test loads besides itself and its conftests ---------------------------------

_LOADCODE_WRITE = (
    "import os\nfrom pathlib import Path\ndef prep():\n    os.environ['LD_PRELOAD'] = str(Path.cwd() / 'preload.so')\n"
)
_LOADCODE_LAUNCH = (
    "import subprocess\ndef launch(argv):\n    return subprocess.run(argv, capture_output=True, text=True)\n"
)
_LOADCODE_TRUE = "    subprocess.run(['/bin/true'], check=True)\n"
# Helpers, plugins and their imports, keyed by path.
_LOADCODE_MODULES = {
    "tests/r8_envhelp.py": _LOADCODE_WRITE,
    "r8_envsupport.py": _LOADCODE_WRITE,
    "r8_launchsupport.py": _LOADCODE_LAUNCH,
    "tests/r8_plug/envplugin.py": (
        "import pytest\nfrom pathlib import Path\n@pytest.fixture\ndef preload(monkeypatch):\n"
        "    monkeypatch.setenv('LD_PRELOAD', str(Path.cwd() / 'preload.so'))\n"
    ),
    "r8_chain_a.py": "from r8_chain_b import prep\n",
    "r8_chain_b.py": "from r8_envsupport import prep\n",
    "r8pkg/__init__.py": "",
    "r8pkg/front.py": "from .back import launch\n",
    "r8pkg/back.py": _LOADCODE_LAUNCH,
    "r8_quiet.py": "import os\ndef flag():\n    return os.environ.get('ROAM_TEST_FLAG', '')\n",
    "tests/r8_quiethelp.py": "import os\ndef flag():\n    return 'PATH' in os.environ\n",
    "src/roam/r8_git.py": "import subprocess\ndef git():\n    return subprocess.run(['git', '--version'], capture_output=True)\n",
}
# Each loads code that writes the environment before /bin/true, or launches a program itself.
_LOADCODE_TESTS = {
    "helper_env": "from tests.r8_envhelp import prep\ndef test_run():\n    prep()\n" + _LOADCODE_TRUE,
    "support_env": "from r8_envsupport import prep\ndef test_run():\n    prep()\n" + _LOADCODE_TRUE,
    "plugin_fixture": "pytest_plugins = ['tests.r8_plug.envplugin']\ndef test_run(preload):\n" + _LOADCODE_TRUE,
    "support_launch": "from r8_launchsupport import launch\ndef test_run():\n    launch(['roam', 'alpha'])\n",
    "chain_env": "import r8_chain_a\ndef test_run():\n    r8_chain_a.prep()\n" + _LOADCODE_TRUE,
    "relative_launch": "from r8pkg.front import launch\ndef test_run():\n    launch(['roam', 'alpha'])\n",
}
# CONTROLS: a helper that neither launches nor writes the environment; a production module that launches.
_LOADCODE_CONTROL_SOURCES = {
    "quiet": "from r8_quiet import flag\ndef test_run():\n    flag()\n" + _LOADCODE_TRUE,
    "quiet_test_side": "from tests.r8_quiethelp import flag\ndef test_run():\n    flag()\n" + _LOADCODE_TRUE,
    "production": "from roam.r8_git import git\ndef test_run():\n    git()\n" + _LOADCODE_TRUE,
}
_LOADCODE_CONTROLS = [f"r8_control_{name}" for name in _LOADCODE_CONTROL_SOURCES]
_LOADCODE_PATHS = {
    **{name: f"tests/test_r8_{name}.py" for name in _LOADCODE_TESTS},
    **{f"r8_control_{name}": f"tests/test_r8_control_{name}.py" for name in _LOADCODE_CONTROL_SOURCES},
}


def _loaded_code_cases(files):
    files.update(_LOADCODE_MODULES)
    for name, body in _LOADCODE_TESTS.items():
        files[_LOADCODE_PATHS[name]] = "import subprocess\n" + body
    for name, body in _LOADCODE_CONTROL_SOURCES.items():
        files[_LOADCODE_PATHS[f"r8_control_{name}"]] = "import subprocess\n" + body


@pytest.mark.parametrize("name", sorted(_LOADCODE_TESTS))
def test_loaded_code_that_launches_or_writes_the_environment_is_possible(project, monkeypatch, name):
    for target in ("alpha_cmd", "beta_cmd"):
        assert _kinds_by_file(_affected(project, monkeypatch, target)).get(_LOADCODE_PATHS[name]) == {"CLI_POSSIBLE"}


@pytest.mark.parametrize("target", ["alpha_cmd", "beta_cmd"])
def test_quiet_helpers_and_production_launches_stay_excluded(project, monkeypatch, target):
    kinds = _kinds_by_file(_affected(project, monkeypatch, target))
    assert not {_LOADCODE_PATHS[name] for name in _LOADCODE_CONTROLS} & set(kinds)


def test_blind_spots_are_named():
    from roam.commands import cmd_affected_tests

    text = " ".join((cmd_affected_tests.__doc__ + cmd_affected_tests.affected_tests_cmd.help).split())
    for phrase in (
        "importlib",
        "__import__",
        "getattr",
        "entry points",
        "-p flag",
        "production code",
        f"{cmd_affected_tests._LOADED_DEPTH} imports",
        "named rather than chased",
        "setup_module",
        "getattr(subprocess, 'run')",
        "request.getfixturevalue",
    ):
        assert phrase in text, phrase


@pytest.mark.skipif(sys.platform != "linux" or not shutil.which("cc"), reason="needs a C compiler and ld.so")
@pytest.mark.parametrize("name", ["helper_env", "support_env", "plugin_fixture", "chain_env"])
def test_environment_fixtures_really_run_alpha(project, tmp_path, name):
    """/bin/true under an LD_PRELOAD set by loaded code runs alpha."""
    subprocess.run(["cc", "-shared", "-fPIC", "-o", "preload.so", "preload.c"], cwd=project, check=True, timeout=60)
    marker = project / "ran_alpha"
    marker.unlink(missing_ok=True)
    config = tmp_path / "pytest.ini"
    config.write_text("[pytest]\n")
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(project), str(project / "src")]))
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-c", str(config), "-p", "no:cacheprovider", _LOADCODE_PATHS[name]],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0 and marker.exists(), proc.stdout + proc.stderr
    marker.unlink()


@pytest.mark.parametrize(
    "name",
    [
        *_ALLOW_EXECUTED,
        *_ALLOW_FLOWS_EXECUTED,
        "unrelated",
        "p5_not_roam",
        *_GITPY_EXECUTED,
        *_GITPY_CONTROLS,
        *_SHELLPATH_EXECUTED,
        *_SHELLPATH_CONTROLS,
        *(f"shell_{name}" for name in _ENVWRITE_SHELLS),
        *(f"env_{name}" for name in _ENVWRITE_ENV if name != "preload"),
        *_ENVWRITE_CONTROLS,
        *_LOADCODE_CONTROLS,
    ],
)
def test_launch_fixtures_really_run(project, tmp_path, name):
    """Each fixture that stands for running alpha does; each control runs and passes."""
    paths = {
        **_GITPY_PATHS,
        **_SHELLPATH_PATHS,
        **_ENVWRITE_PATHS,
        **_LOADCODE_PATHS,
        "p5_not_roam": "tests/test_p5_not_roam.py",
    }
    rel = paths.get(name, f"tests/test_r4_{name}.py")
    config = tmp_path / "pytest.ini"
    config.write_text("[pytest]\n")
    env = dict(os.environ, PYTHONPATH=str(project / "src"), PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    env.pop("CI", None)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-c", str(config), "-p", "no:cacheprovider", rel],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


# -- Fixtures, test-side helper modules and TYPE_CHECKING imports ------------------------------------

_RESOLVE_ALPHA = (
    "    out = subprocess.run([sys.executable, '-m', 'roam', 'alpha'], capture_output=True, text=True)\n"
    "    assert 'alpha-ran' in out.stdout, out.stderr\n"
)
_RESOLVE_FIXTURE = "import subprocess, sys\nimport pytest\n@pytest.fixture{args}\ndef {name}({params}):\n"
_RESOLVE_MODULES = {
    "tests/r9c/conftest.py": (
        _RESOLVE_FIXTURE.format(args="", name="srv", params="")
        + _RESOLVE_ALPHA
        + "@pytest.fixture\ndef outer(srv):\n    return None\n"
        + "@pytest.fixture(name='renamed')\ndef _renamed_fixture():\n"
        + _RESOLVE_ALPHA
    ),
    "tests/r9a/conftest.py": _RESOLVE_FIXTURE.format(args="(autouse=True)", name="srv", params="") + _RESOLVE_ALPHA,
    "tests/r9cx/conftest.py": "import pytest\n@pytest.fixture\ndef srv():\n    return None\n",
    "tests/r9_importlaunch.py": "import subprocess, sys\n" + _RESOLVE_ALPHA.replace("    ", ""),
    "tests/r9_runner.py": (
        "import subprocess\nclass Runner:\n    def run(self, argv):\n"
        "        out = subprocess.run(argv, capture_output=True, text=True)\n"
        "        assert 'alpha-ran' in out.stdout, out.stderr\n"
        "def launch(argv):\n    return Runner().run(argv)\n"
    ),
}
# Each runs alpha through a conftest fixture: requested by name, autouse, through another fixture, by mark or alias.
_RESOLVE_FIXTURE_TESTS = {
    "tests/r9c/test_r9_fixture_by_name.py": "def test_run(srv):\n    pass\n",
    "tests/r9a/test_r9_fixture_autouse.py": "def test_run():\n    pass\n",
    "tests/r9c/test_r9_fixture_chain.py": "def test_run(outer):\n    pass\n",
    "tests/r9c/test_r9_fixture_mark.py": "import pytest\n@pytest.mark.usefixtures('srv')\ndef test_run():\n    pass\n",
    "tests/r9c/test_r9_fixture_renamed.py": "def test_run(renamed):\n    pass\n",
    # A class-body override applies only inside that class; the module-level test still runs the conftest fixture.
    "tests/r9c/test_r9_fixture_class_override.py": (
        "import pytest\nclass TestA:\n    @pytest.fixture\n    def srv(self):\n        return None\n"
        "    def test_a(self, srv):\n        pass\ndef test_run(srv):\n    pass\n"
    ),
    "tests/r9c/test_r9_fixture_indirect.py": (
        "import pytest\n@pytest.mark.parametrize('srv', [1], indirect=True)\ndef test_run(srv):\n    pass\n"
    ),
}
# Each runs alpha through a test-side helper module: on import, or through a method.
_RESOLVE_HELPER_TESTS = {
    "tests/test_r9_helper_import_time.py": "def test_run():\n    import tests.r9_importlaunch\n",
    "tests/test_r9_helper_method.py": (
        "import sys\nfrom tests.r9_runner import Runner\n"
        "def test_run():\n    Runner().run([sys.executable, '-m', 'roam', 'alpha'])\n"
    ),
}
# Each imports the alpha module only for a type checker: the import never runs.
_RESOLVE_TYPE_CHECKING = {
    "tests/test_r9_type_checking.py": (
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from roam.commands.cmd_alpha import alpha_cmd\n"
    ),
    "tests/test_r9_typing_type_checking.py": "import typing\nif typing.TYPE_CHECKING:\n    import roam.commands.cmd_alpha\n",
}
# CONTROLS: a fixture the test does not request, one the module overrides, one from a sibling folder's conftest.
_RESOLVE_CONTROLS = {
    "tests/r9c/test_r9_control_unrequested.py": "def test_run():\n    pass\n",
    "tests/r9c/test_r9_control_overridden.py": (
        "import pytest\n@pytest.fixture\ndef srv():\n    return None\ndef test_run(srv):\n    pass\n"
    ),
    "tests/r9cx/test_r9_control_sibling.py": "def test_run(srv):\n    pass\n",
}
# Fixtures that never run: a name parametrize binds directly, a request made only by a fixture nothing uses.
_RESOLVE_UNRUN_FIXTURES = {
    "tests/r9c/test_r9_control_parametrized.py": (
        "import pytest\n@pytest.mark.parametrize('srv', [1])\ndef test_run(srv):\n    pass\n"
    ),
    "tests/r9c/test_r9_control_unused_fixture.py": (
        "import pytest\n@pytest.fixture\ndef unused(srv):\n    return None\ndef test_run():\n    pass\n"
    ),
}
# CONTROL: an import outside the TYPE_CHECKING branch still runs.
_RESOLVE_IMPORT_CONTROL = (
    "tests/test_r9_control_import.py",
    "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    pass\nelse:\n"
    "    from roam.commands.cmd_alpha import alpha_cmd\n",
)


def _fixture_helper_cases(files):
    files.update(_RESOLVE_MODULES)
    files.update(_RESOLVE_FIXTURE_TESTS)
    files.update(_RESOLVE_HELPER_TESTS)
    files.update(_RESOLVE_CONTROLS)
    files.update(_RESOLVE_UNRUN_FIXTURES)
    for rel, head in [*_RESOLVE_TYPE_CHECKING.items(), _RESOLVE_IMPORT_CONTROL]:
        files[rel] = head + "def test_run():\n    pass\n"


@pytest.mark.parametrize("rel", sorted(_RESOLVE_FIXTURE_TESTS))
def test_a_conftest_fixture_counts_for_the_tests_it_applies_to(project, monkeypatch, rel):
    assert _kinds_by_file(_affected(project, monkeypatch, "alpha_cmd")).get(rel) == {"CLI_INVOKE"}
    assert rel not in _kinds_by_file(_affected(project, monkeypatch, "beta_cmd"))


@pytest.mark.parametrize("rel", sorted(_RESOLVE_HELPER_TESTS))
def test_a_launch_in_a_test_side_helper_module_is_possible(project, monkeypatch, rel):
    assert _kinds_by_file(_affected(project, monkeypatch, "alpha_cmd")).get(rel) in ({"CLI_POSSIBLE"}, {"CLI_INVOKE"})
    assert _kinds_by_file(_affected(project, monkeypatch, "beta_cmd")).get(rel) == {"CLI_POSSIBLE"}


@pytest.mark.parametrize("rel", sorted(_RESOLVE_TYPE_CHECKING))
def test_a_type_checking_import_is_not_module_import(project, monkeypatch, rel):
    assert "MODULE_IMPORT" not in _kinds_by_file(_affected(project, monkeypatch, "alpha_cmd")).get(rel, set())


@pytest.mark.parametrize("target", ["alpha_cmd", "beta_cmd"])
def test_unrequested_overridden_and_sibling_fixtures_stay_excluded(project, monkeypatch, target):
    kinds = _kinds_by_file(_affected(project, monkeypatch, target))
    assert not set(_RESOLVE_CONTROLS) & set(kinds)


@pytest.mark.parametrize("rel", sorted(_RESOLVE_UNRUN_FIXTURES))
def test_a_fixture_that_never_runs_proves_nothing(project, monkeypatch, rel):
    assert rel not in _kinds_by_file(_affected(project, monkeypatch, "alpha_cmd"))
    assert rel not in _kinds_by_file(_affected(project, monkeypatch, "beta_cmd"))


def test_an_import_outside_type_checking_stays_module_import(project, monkeypatch):
    kinds = _kinds_by_file(_affected(project, monkeypatch, "alpha_cmd"))
    assert kinds.get(_RESOLVE_IMPORT_CONTROL[0]) == {"MODULE_IMPORT"}


@pytest.mark.parametrize(
    "rel",
    sorted(
        [
            *_RESOLVE_FIXTURE_TESTS,
            *_RESOLVE_HELPER_TESTS,
            *_RESOLVE_TYPE_CHECKING,
            *_RESOLVE_CONTROLS,
            *_RESOLVE_UNRUN_FIXTURES,
            _RESOLVE_IMPORT_CONTROL[0],
        ]
    ),
)
def test_fixture_and_helper_fixtures_really_run(project, tmp_path, rel):
    """Each fixture that stands for running alpha asserts alpha ran; each control runs and passes."""
    config = tmp_path / "pytest.ini"
    config.write_text("[pytest]\n")
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(project), str(project / "src")]))
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-c", str(config), "-p", "no:cacheprovider", rel],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
