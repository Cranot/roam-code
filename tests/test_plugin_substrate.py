"""Plugin substrate tests — RoamPlugin / RoamPluginContext contract.

Covers the typed contract added for R25 (pluggable analyzers):

- :func:`discover_plugins` returns the registered plugin list.
- Each ``register_*`` hook on :class:`RoamPluginContext` records its
  contribution against the right plugin and the right state slot.
- A broken plugin's exception is caught and surfaced via
  :func:`get_plugin_errors` — discovery never propagates the failure.
- ``roam plugins list / doctor`` shell out cleanly under both bare-text
  and ``--json`` modes, even on a clean install with zero plugins.

Most tests fake the entry-point scan with the ``ROAM_PLUGIN_MODULES``
env channel because it's the lightest-weight way to inject test
plugins without installing distributions.
"""

from __future__ import annotations

import importlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _reset_plugin_runtime():
    """Reset plugin state + reload cli so freshly-registered commands appear."""
    import roam.cli as cli_mod
    import roam.languages.registry as registry
    import roam.plugins as plugins

    plugins._reset_plugin_state_for_tests()
    registry._create_extractor.cache_clear()
    importlib.reload(cli_mod)
    return cli_mod


def _write_plugin(tmp_path: Path, module_name: str, body: str) -> str:
    """Write a single-file plugin module under tmp_path and return the module name."""
    plugin_path = tmp_path / f"{module_name}.py"
    plugin_path.write_text(body, encoding="utf-8")
    return module_name


@pytest.fixture(autouse=True)
def _reset_state_between_tests(monkeypatch):
    """Every test starts with a pristine plugin registry."""
    monkeypatch.delenv("ROAM_PLUGIN_MODULES", raising=False)
    import roam.plugins as plugins

    plugins._reset_plugin_state_for_tests()
    yield
    plugins._reset_plugin_state_for_tests()
    # Drop loaded test plugins so subsequent tests don't see them.
    for name in list(sys.modules):
        if name.startswith("roam_substrate_"):
            sys.modules.pop(name, None)


# ---------------------------------------------------------------------------
# 1. Empty-install discovery
# ---------------------------------------------------------------------------


def test_discover_plugins_returns_empty_on_clean_install(monkeypatch):
    """No env modules + no entry points -> no plugins, no errors."""
    from roam.plugins import discover_plugins, get_plugin_errors

    plugins = discover_plugins()
    assert plugins == []
    assert get_plugin_errors() == []


def test_discover_plugins_is_idempotent(monkeypatch):
    """Repeated discovery calls do not re-import modules or accumulate state."""
    from roam.plugins import discover_plugins, get_plugin_errors

    first = discover_plugins()
    second = discover_plugins()
    assert first == second
    assert get_plugin_errors() == []


# ---------------------------------------------------------------------------
# 2. Framework detector hook
# ---------------------------------------------------------------------------


def test_register_framework_detector_hook_invoked(monkeypatch, tmp_path):
    """A registered framework detector receives a Path and is consulted."""
    module_name = _write_plugin(
        tmp_path,
        "roam_substrate_framework_plugin",
        "from pathlib import Path\n"
        "calls = []\n"
        "\n"
        "def detect_fw(root):\n"
        "    calls.append(root)\n"
        "    # Returns a slug only when a sentinel file exists.\n"
        "    return 'demo-fw' if (root / 'DEMO').exists() else None\n"
        "\n"
        "def register(ctx):\n"
        "    ctx.declare(name='fw-demo', version='0.0.1', description='demo')\n"
        "    ctx.register_framework_detector(detect_fw)\n",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("ROAM_PLUGIN_MODULES", module_name)

    _reset_plugin_runtime()
    from roam.plugins import get_plugin_framework_detectors, get_plugins

    plugins = get_plugins()
    assert any(p.name == "fw-demo" for p in plugins)
    detectors = get_plugin_framework_detectors()
    assert len(detectors) == 1

    # The detector itself is callable and respects its signal.
    root_no_signal = tmp_path / "no_signal"
    root_no_signal.mkdir()
    assert detectors[0](root_no_signal) is None

    root_with_signal = tmp_path / "with_signal"
    root_with_signal.mkdir()
    (root_with_signal / "DEMO").write_text("x", encoding="utf-8")
    assert detectors[0](root_with_signal) == "demo-fw"


# ---------------------------------------------------------------------------
# 3. Language extractor routing
# ---------------------------------------------------------------------------


def test_register_extractor_routes_to_correct_language(monkeypatch, tmp_path):
    """A plugin-registered extractor wins for its declared extension."""
    module_name = _write_plugin(
        tmp_path,
        "roam_substrate_lang_plugin",
        "from roam.languages.base import LanguageExtractor\n"
        "\n"
        "class TomlExtractor(LanguageExtractor):\n"
        "    @property\n"
        "    def language_name(self):\n"
        "        return 'demo-toml'\n"
        "    @property\n"
        "    def file_extensions(self):\n"
        "        return ['.dtoml']\n"
        "    def extract_symbols(self, tree, source, file_path):\n"
        "        return []\n"
        "    def extract_references(self, tree, source, file_path):\n"
        "        return []\n"
        "\n"
        "def register(ctx):\n"
        "    ctx.register_language_extractor(\n"
        "        'demo-toml', TomlExtractor, extensions=['.dtoml'], grammar_alias='toml'\n"
        "    )\n",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("ROAM_PLUGIN_MODULES", module_name)

    _reset_plugin_runtime()
    from roam.languages.registry import (
        get_extractor_for_file,
        get_language_for_file,
        get_supported_extensions,
        get_supported_languages,
    )

    assert get_language_for_file("project/example.dtoml") == "demo-toml"
    assert ".dtoml" in get_supported_extensions()
    assert "demo-toml" in get_supported_languages()

    extractor = get_extractor_for_file("project/example.dtoml")
    assert extractor is not None
    assert extractor.language_name == "demo-toml"


# ---------------------------------------------------------------------------
# 4. Bad plugin fails gracefully
# ---------------------------------------------------------------------------


def test_plugin_with_bad_entry_point_fails_gracefully(monkeypatch, tmp_path):
    """A plugin that raises during register() never breaks core discovery."""
    module_name = _write_plugin(
        tmp_path,
        "roam_substrate_broken_plugin",
        "def register(ctx):\n"
        "    raise RuntimeError('boom')\n",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("ROAM_PLUGIN_MODULES", module_name)

    _reset_plugin_runtime()
    from roam.plugins import discover_plugins, get_plugin_commands, get_plugin_errors

    # Discovery still returns (no exception bubbles out).
    discover_plugins()
    errors = get_plugin_errors()
    assert errors, "expected the broken plugin to surface a discovery error"
    assert any("boom" in e for e in errors)
    # And core registry slots are unaffected.
    assert get_plugin_commands() == {}


def test_unimportable_module_is_surfaced_as_error(monkeypatch):
    """A non-existent module on ROAM_PLUGIN_MODULES becomes an error string."""
    monkeypatch.setenv("ROAM_PLUGIN_MODULES", "roam_substrate_does_not_exist_xyz")
    _reset_plugin_runtime()
    from roam.plugins import discover_plugins, get_plugin_errors

    discover_plugins()
    errors = get_plugin_errors()
    assert any("import failed" in e for e in errors)


# ---------------------------------------------------------------------------
# 5. ``roam plugins doctor`` surfaces failures
# ---------------------------------------------------------------------------


def test_plugins_doctor_reports_failed_loads(monkeypatch, tmp_path):
    """``roam plugins doctor`` exits non-zero and prints the error string."""
    module_name = _write_plugin(
        tmp_path,
        "roam_substrate_doctor_plugin",
        "def register(ctx):\n"
        "    raise ValueError('synthetic failure')\n",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("ROAM_PLUGIN_MODULES", module_name)

    cli_mod = _reset_plugin_runtime()
    runner = CliRunner()
    result = runner.invoke(cli_mod.cli, ["plugins", "doctor"], catch_exceptions=False)

    assert result.exit_code == 5, result.output
    assert "synthetic failure" in result.output
    assert "VERDICT" in result.output


def test_plugins_doctor_clean_install_exits_zero(monkeypatch):
    """No plugins + no errors -> doctor reports ``all plugins loaded cleanly``."""
    cli_mod = _reset_plugin_runtime()
    runner = CliRunner()
    result = runner.invoke(cli_mod.cli, ["plugins", "doctor"], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    assert "all plugins loaded cleanly" in result.output


# ---------------------------------------------------------------------------
# 6. ``roam plugins list`` envelope shape
# ---------------------------------------------------------------------------


def test_plugins_list_envelope_shape_clean(monkeypatch):
    """``--json plugins list`` returns a well-formed envelope with empty fields."""
    cli_mod = _reset_plugin_runtime()
    runner = CliRunner()
    result = runner.invoke(cli_mod.cli, ["--json", "plugins", "list"], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["command"] == "plugins"
    summary = payload["summary"]

    # The envelope MUST be self-describing even when empty — Pattern 1
    # of the dogfood synthesis (no JSON-parse-on-empty-input).
    for key in (
        "verdict",
        "plugins",
        "commands",
        "detectors",
        "languages",
        "extensions",
        "framework_detectors",
        "bridges",
        "errors",
    ):
        assert key in summary, f"summary missing key: {key}"

    for top in ("plugins", "commands", "detectors", "languages", "bridges", "errors"):
        assert top in payload
    assert isinstance(payload["plugins"], list)
    assert isinstance(payload["commands"], list)


def test_plugins_list_envelope_shape_with_plugin(monkeypatch, tmp_path):
    """A registered plugin shows up in the envelope's ``plugins`` list."""
    module_name = _write_plugin(
        tmp_path,
        "roam_substrate_envelope_plugin",
        "def register(ctx):\n"
        "    ctx.declare(name='envelope-demo', version='1.2.3', description='shape check')\n"
        "    ctx.register_detector('shape-task', 'naive', lambda _c: [])\n",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("ROAM_PLUGIN_MODULES", module_name)

    cli_mod = _reset_plugin_runtime()
    runner = CliRunner()
    result = runner.invoke(cli_mod.cli, ["--json", "plugins", "list"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)

    plugin_names = [p["name"] for p in payload["plugins"]]
    assert "envelope-demo" in plugin_names
    demo = next(p for p in payload["plugins"] if p["name"] == "envelope-demo")
    assert demo["version"] == "1.2.3"
    assert "detector" in demo["capabilities"]
    assert "shape-task/naive" in payload["detectors"]


# ---------------------------------------------------------------------------
# 7. Bridge registration sanity-check
# ---------------------------------------------------------------------------


def test_register_bridge_records_bridge(monkeypatch, tmp_path):
    """A plugin-supplied bridge appears in ``get_plugin_bridges``."""
    module_name = _write_plugin(
        tmp_path,
        "roam_substrate_bridge_plugin",
        "class FakeBridge:\n"
        "    name = 'fakebridge'\n"
        "    def detect(self, files): return False\n"
        "    def resolve(self, *a, **kw): return []\n"
        "\n"
        "def register(ctx):\n"
        "    ctx.register_bridge(FakeBridge())\n",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("ROAM_PLUGIN_MODULES", module_name)

    _reset_plugin_runtime()
    from roam.plugins import get_plugin_bridges

    bridges = get_plugin_bridges()
    assert any(getattr(b, "name", None) == "fakebridge" for b in bridges)


def test_bridge_missing_required_attribute_raises_inside_plugin(monkeypatch, tmp_path):
    """A bad bridge object is recorded as a plugin error (no core crash)."""
    module_name = _write_plugin(
        tmp_path,
        "roam_substrate_bad_bridge_plugin",
        "class HalfBridge:\n"
        "    name = 'half'\n"
        "    # Missing detect/resolve.\n"
        "\n"
        "def register(ctx):\n"
        "    ctx.register_bridge(HalfBridge())\n",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("ROAM_PLUGIN_MODULES", module_name)

    _reset_plugin_runtime()
    from roam.plugins import get_plugin_bridges, get_plugin_errors

    bridges = get_plugin_bridges()
    assert all(getattr(b, "name", None) != "half" for b in bridges)
    assert any("bridge missing required attribute" in e for e in get_plugin_errors())


# ---------------------------------------------------------------------------
# 8. Detector findings flow through catalog pipeline
# ---------------------------------------------------------------------------


def test_plugin_detector_findings_reach_catalog_runner(monkeypatch, tmp_path):
    """A plugin's detector is invoked by ``catalog.detectors.run_detectors``."""
    module_name = _write_plugin(
        tmp_path,
        "roam_substrate_finding_plugin",
        "def detect(_conn):\n"
        "    return [{\n"
        "        'task_id': 'substrate-task',\n"
        "        'detected_way': 'naive',\n"
        "        'suggested_way': 'better',\n"
        "        'symbol_id': None,\n"
        "        'symbol_name': 'demo.symbol',\n"
        "        'kind': 'function',\n"
        "        'location': 'demo.py:1',\n"
        "        'confidence': 'high',\n"
        "        'reason': 'substrate finding fired',\n"
        "    }]\n"
        "\n"
        "def register(ctx):\n"
        "    ctx.register_detector('substrate-task', 'naive', detect)\n",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("ROAM_PLUGIN_MODULES", module_name)

    _reset_plugin_runtime()
    from roam.catalog.detectors import run_detectors

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    findings = run_detectors(conn, task_filter="substrate-task", profile="aggressive")
    assert findings
    assert findings[0]["task_id"] == "substrate-task"
    assert findings[0]["reason"] == "substrate finding fired"
