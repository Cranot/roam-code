"""Plugin discovery tests for commands, detectors, and language extractors."""

from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner


def _write_test_plugin(tmp_path: Path, module_name: str = "roam_test_plugin") -> str:
    plugin_path = tmp_path / f"{module_name}.py"
    plugin_path.write_text(
        "import click\n"
        "from roam.languages.base import LanguageExtractor\n"
        "\n"
        "@click.command('hello-plugin')\n"
        "def hello_plugin():\n"
        "    click.echo('plugin-ok')\n"
        "\n"
        "def detect_plugin(_conn):\n"
        "    return [{\n"
        "        'task_id': 'plugin-task',\n"
        "        'detected_way': 'naive',\n"
        "        'suggested_way': 'better',\n"
        "        'symbol_id': None,\n"
        "        'symbol_name': 'plugin.symbol',\n"
        "        'kind': 'function',\n"
        "        'location': 'plugin.py:1',\n"
        "        'confidence': 'high',\n"
        "        'reason': 'plugin detector fired',\n"
        "    }]\n"
        "\n"
        "class MiniExtractor(LanguageExtractor):\n"
        "    @property\n"
        "    def language_name(self):\n"
        "        return 'mini'\n"
        "\n"
        "    @property\n"
        "    def file_extensions(self):\n"
        "        return ['.mini']\n"
        "\n"
        "    def extract_symbols(self, tree, source, file_path):\n"
        "        return []\n"
        "\n"
        "    def extract_references(self, tree, source, file_path):\n"
        "        return []\n"
        "\n"
        "def register(api):\n"
        "    api.register_command('hello-plugin', __name__, 'hello_plugin')\n"
        "    api.register_detector('plugin-task', 'naive', detect_plugin)\n"
        "    api.register_language_extractor(\n"
        "        'mini', MiniExtractor, extensions=['.mini'], grammar_alias='python'\n"
        "    )\n",
        encoding="utf-8",
    )
    return module_name


def _reset_plugin_runtime():
    import roam.cli as cli_mod
    import roam.languages.registry as registry
    import roam.plugins as plugins

    plugins._reset_plugin_state_for_tests()
    registry._create_extractor.cache_clear()
    importlib.reload(cli_mod)
    return cli_mod


def test_plugin_command_discovery_via_env(monkeypatch, tmp_path):
    module_name = _write_test_plugin(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("ROAM_PLUGIN_MODULES", module_name)

    cli_mod = _reset_plugin_runtime()
    runner = CliRunner()
    result = runner.invoke(cli_mod.cli, ["hello-plugin"], catch_exceptions=False)

    assert result.exit_code == 0
    assert "plugin-ok" in result.output
    assert "hello-plugin" in cli_mod.cli.list_commands(None)


def test_plugin_commands_do_not_mutate_the_shipped_command_map(monkeypatch, tmp_path):
    """A plugin extends the runtime surface without touching the shipped one.

    ``roam.cli._COMMANDS`` is the command map this build ships, and a lot of
    the repo treats it as exactly that: the surface and compatibility
    baselines, the budget-coverage and capability-decoration gates, and the
    W1331 disclosure enumeration, which can only scan files that exist under
    ``src/roam/commands``. Merging discovered plugin entries into it made
    every one of those answers depend on whether something earlier in the
    process had tripped plugin discovery. Two of them reached CI as flakes
    (W420's bouncing command_count, W1331's unscannable module), and both
    were patched at the consumer, which left the trap set for the next one.

    So the invariant is pinned at the source: discovery populates the
    ``_PLUGIN_COMMANDS`` overlay, the shipped literal does not move, and the
    plugin command is still fully invokable.
    """
    module_name = _write_test_plugin(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("ROAM_PLUGIN_MODULES", module_name)

    cli_mod = _reset_plugin_runtime()
    shipped_before = dict(cli_mod._COMMANDS)

    result = CliRunner().invoke(cli_mod.cli, ["hello-plugin"], catch_exceptions=False)
    assert result.exit_code == 0

    assert cli_mod._COMMANDS == shipped_before, "plugin discovery mutated the shipped command map"
    assert "hello-plugin" not in cli_mod._COMMANDS

    # Every shipped command resolves to a module the disclosure scanner can
    # actually reach. This is the W1331 denominator stated at its source.
    foreign = sorted({m for m, _attr in cli_mod._COMMANDS.values() if not m.startswith("roam.commands.")})
    assert not foreign, f"shipped commands registered outside roam.commands: {foreign}"

    # ...and the plugin is still a real command, not merely recorded.
    assert cli_mod._PLUGIN_COMMANDS["hello-plugin"] == (module_name, "hello_plugin")
    assert cli_mod._command_target("hello-plugin") == (module_name, "hello_plugin")
    assert "hello-plugin" in cli_mod.cli.list_commands(None)


def test_plugin_detector_discovery_via_env(monkeypatch, tmp_path):
    module_name = _write_test_plugin(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("ROAM_PLUGIN_MODULES", module_name)

    _reset_plugin_runtime()
    from roam.catalog.detectors import run_detectors

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    findings = run_detectors(conn, task_filter="plugin-task", profile="aggressive")

    assert findings
    assert findings[0]["task_id"] == "plugin-task"
    assert findings[0]["reason"] == "plugin detector fired"


def test_plugin_language_extractor_and_extension_discovery(monkeypatch, tmp_path):
    module_name = _write_test_plugin(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("ROAM_PLUGIN_MODULES", module_name)

    _reset_plugin_runtime()
    from roam.index.parser import detect_language, parse_file
    from roam.languages.registry import (
        get_extractor,
        get_language_for_file,
        get_supported_extensions,
        get_supported_languages,
    )

    sample = tmp_path / "example.mini"
    sample.write_text("def f():\n    return 1\n", encoding="utf-8")

    assert detect_language(str(sample)) == "mini"
    assert get_language_for_file("example.mini") == "mini"
    assert ".mini" in get_supported_extensions()
    assert "mini" in get_supported_languages()

    extractor = get_extractor("mini")
    assert extractor.language_name == "mini"

    tree, source, lang = parse_file(sample)
    assert tree is not None
    assert source is not None
    assert lang == "mini"


def test_parser_plugin_language_discovery_errors_propagate(monkeypatch):
    import roam.plugins as plugins
    from roam.index import parser

    def fail_extension_discovery():
        raise RuntimeError("plugin registry failed")

    def fail_alias_discovery():
        raise RuntimeError("plugin alias registry failed")

    monkeypatch.setattr(plugins, "get_plugin_language_extensions", fail_extension_discovery)

    with pytest.raises(RuntimeError, match="plugin registry failed"):
        parser._plugin_language_extensions()

    monkeypatch.setattr(plugins, "get_plugin_language_grammar_aliases", fail_alias_discovery)

    with pytest.raises(RuntimeError, match="plugin alias registry failed"):
        parser._plugin_grammar_aliases()
