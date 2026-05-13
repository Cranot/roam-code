"""Validates the R25 plugin substrate end-to-end via the Rails reference plugin.

Companion to ``tests/test_plugin_substrate.py`` — that file uses
inline-written test plugins to assert each registry hook in
isolation. This file asserts the same substrate against an actual
on-disk plugin (``dev/example-plugin/roam_plugin_rails``) so the
end-to-end discovery path (PYTHONPATH + ``ROAM_PLUGIN_MODULES``)
exercised by plugin authors is covered too.

If every test in this file passes:

- ``register(ctx)`` resolves and runs without exceptions.
- ``register_framework_detector`` actually adds the callable to the
  registry state slot.
- ``autodetect_framework_profile()`` consults plugin-contributed
  detectors when no built-in profile matches.
- PYTHONPATH-based plugin discovery (env channel) works the way the
  plugin-author docs claim it does.
- ``roam plugins list / info / doctor`` reflect the live plugin
  state in subprocess invocations (not just in-process API calls).

This is the W25.1 dogfood promised against the W22.5 substrate.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

# ``dev/example-plugin`` houses both the W22.5 reference stub
# (``roam_plugin_example``) and this dogfood plugin
# (``roam_plugin_rails``). We prepend the directory to PYTHONPATH so
# import-time discovery finds them without a ``pip install``.
PLUGIN_DIR = Path(__file__).resolve().parent.parent / "dev" / "example-plugin"
PLUGIN_MODULE = "roam_plugin_rails"


# ---------------------------------------------------------------------------
# Direct-import path: assert the plugin module is well-formed.
# ---------------------------------------------------------------------------


def test_plugin_module_imports_and_exposes_register():
    """The plugin file on disk has a callable ``register`` symbol."""
    import importlib

    sys.path.insert(0, str(PLUGIN_DIR))
    try:
        # Clear any cached copy so this test runs cleanly regardless
        # of ordering relative to other tests that may have imported it.
        sys.modules.pop(PLUGIN_MODULE, None)
        module = importlib.import_module(PLUGIN_MODULE)
        assert callable(getattr(module, "register", None))
        assert callable(getattr(module, "detect_rails", None))
    finally:
        sys.path.remove(str(PLUGIN_DIR))
        sys.modules.pop(PLUGIN_MODULE, None)


def test_detect_rails_recognises_gemfile_with_rails_gem(tmp_path):
    """The exported ``detect_rails`` callable matches a real Gemfile."""
    sys.path.insert(0, str(PLUGIN_DIR))
    try:
        sys.modules.pop(PLUGIN_MODULE, None)
        import importlib

        module = importlib.import_module(PLUGIN_MODULE)
        (tmp_path / "Gemfile").write_text("source 'https://rubygems.org'\ngem 'rails', '~> 7.0'\n", encoding="utf-8")
        assert module.detect_rails(tmp_path) == "rails"
    finally:
        sys.path.remove(str(PLUGIN_DIR))
        sys.modules.pop(PLUGIN_MODULE, None)


def test_detect_rails_returns_none_without_gemfile(tmp_path):
    """A bare directory does NOT trip the Rails detector."""
    sys.path.insert(0, str(PLUGIN_DIR))
    try:
        sys.modules.pop(PLUGIN_MODULE, None)
        import importlib

        module = importlib.import_module(PLUGIN_MODULE)
        assert module.detect_rails(tmp_path) is None
    finally:
        sys.path.remove(str(PLUGIN_DIR))
        sys.modules.pop(PLUGIN_MODULE, None)


def test_detect_rails_returns_none_for_non_rails_gemfile(tmp_path):
    """A Gemfile that lists Sinatra (not Rails) returns None."""
    sys.path.insert(0, str(PLUGIN_DIR))
    try:
        sys.modules.pop(PLUGIN_MODULE, None)
        import importlib

        module = importlib.import_module(PLUGIN_MODULE)
        (tmp_path / "Gemfile").write_text("source 'https://rubygems.org'\ngem 'sinatra'\n", encoding="utf-8")
        assert module.detect_rails(tmp_path) is None
    finally:
        sys.path.remove(str(PLUGIN_DIR))
        sys.modules.pop(PLUGIN_MODULE, None)


# ---------------------------------------------------------------------------
# Discovery path: env-channel ``ROAM_PLUGIN_MODULES`` loads the plugin
# and the registry surface reflects the contribution.
# ---------------------------------------------------------------------------


def test_plugin_registers_via_env_channel(monkeypatch):
    """``ROAM_PLUGIN_MODULES=roam_plugin_rails`` populates the registry."""
    monkeypatch.syspath_prepend(str(PLUGIN_DIR))
    monkeypatch.setenv("ROAM_PLUGIN_MODULES", PLUGIN_MODULE)

    import roam.plugins as plugins

    plugins._reset_plugin_state_for_tests()
    sys.modules.pop(PLUGIN_MODULE, None)

    try:
        loaded = plugins.discover_plugins()
        names = [p.name for p in loaded]
        assert "rails" in names, f"expected rails in discovered plugins, got {names}"

        # The plugin contributes exactly one framework detector.
        fw_detectors = plugins.get_plugin_framework_detectors()
        assert len(fw_detectors) >= 1
        # Discovery surfaced zero errors.
        assert plugins.get_plugin_errors() == []
    finally:
        plugins._reset_plugin_state_for_tests()
        sys.modules.pop(PLUGIN_MODULE, None)


def test_autodetect_consults_plugin_detector_when_core_misses(monkeypatch, tmp_path):
    """``autodetect_framework_profile`` falls through to plugin detectors.

    Builds a synthetic project root where:
    - no ``package.json`` / ``composer.json`` / ``requirements.txt`` /
      ``pyproject.toml`` exists (so all core checks fall through),
    - but the plugin's own detector returns a slug.

    We swap in a single plugin detector that returns ``"plugin-rails"``
    so we can distinguish the plugin path from the core path
    unambiguously (a real ``Gemfile`` would also match the core check).
    """
    monkeypatch.syspath_prepend(str(PLUGIN_DIR))
    monkeypatch.setenv("ROAM_PLUGIN_MODULES", PLUGIN_MODULE)
    monkeypatch.chdir(tmp_path)

    import roam.plugins as plugins

    plugins._reset_plugin_state_for_tests()
    sys.modules.pop(PLUGIN_MODULE, None)

    try:
        # Force the registered detector to return a unique sentinel so
        # we know the plugin path fired (not a built-in shadow).
        plugins.discover_plugins()
        state = plugins._registry_state()
        state.framework_detectors = [lambda root: "plugin-rails"]

        from roam.catalog.detectors import autodetect_framework_profile

        result = autodetect_framework_profile()
        assert result == "plugin-rails", (
            f"expected plugin detector to fire after core misses, got {result!r}"
        )
    finally:
        plugins._reset_plugin_state_for_tests()
        sys.modules.pop(PLUGIN_MODULE, None)


def test_rails_detected_with_plugin(monkeypatch, tmp_path):
    """Path A: with the plugin loaded, a Gemfile project resolves to ``rails``.

    W28.2 cut Rails detection out of core. The plugin is now the
    *only* path that produces ``"rails"`` from a Gemfile fixture —
    this test guards that path against regression.
    """
    monkeypatch.syspath_prepend(str(PLUGIN_DIR))
    monkeypatch.setenv("ROAM_PLUGIN_MODULES", PLUGIN_MODULE)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "Gemfile").write_text("gem 'rails'\n", encoding="utf-8")

    import roam.plugins as plugins

    plugins._reset_plugin_state_for_tests()
    sys.modules.pop(PLUGIN_MODULE, None)

    try:
        from roam.catalog.detectors import autodetect_framework_profile

        result = autodetect_framework_profile()
        assert result == "rails", (
            f"expected plugin detector to resolve Gemfile to 'rails', got {result!r}"
        )
    finally:
        plugins._reset_plugin_state_for_tests()
        sys.modules.pop(PLUGIN_MODULE, None)


def test_rails_not_detected_without_plugin(monkeypatch, tmp_path):
    """Path A: with no plugin loaded, a Rails project is NOT detected.

    Confirms detection has been fully extracted from core. The only
    way ``autodetect_framework_profile`` can return ``"rails"`` is
    through the plugin — when ``ROAM_PLUGIN_MODULES`` is unset the
    same Gemfile fixture resolves to ``None``.
    """
    # Be defensive: clear any plugin discovery env so this subprocess-
    # free in-process test can't accidentally pick up the plugin from
    # an outer ``conftest`` or shell environment.
    monkeypatch.delenv("ROAM_PLUGIN_MODULES", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "Gemfile").write_text("source 'https://rubygems.org'\ngem 'rails'\n", encoding="utf-8")

    import roam.plugins as plugins

    plugins._reset_plugin_state_for_tests()
    sys.modules.pop(PLUGIN_MODULE, None)

    try:
        from roam.catalog.detectors import autodetect_framework_profile

        result = autodetect_framework_profile()
        assert result is None, (
            f"core should be silent on Rails without the plugin; got {result!r}"
        )
    finally:
        plugins._reset_plugin_state_for_tests()
        sys.modules.pop(PLUGIN_MODULE, None)


# ---------------------------------------------------------------------------
# Subprocess path: ``roam plugins {list,info,doctor}`` reflects state.
# ---------------------------------------------------------------------------


def _env_with_plugin() -> dict[str, str]:
    """Build an env dict that exposes the dogfood plugin to a subprocess."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PLUGIN_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    env["ROAM_PLUGIN_MODULES"] = PLUGIN_MODULE
    return env


def _roam_available() -> bool:
    """Skip subprocess tests when the ``roam`` console script is missing.

    Happens on a clean dev checkout where the package hasn't been
    ``pip install -e .``-ed. We still cover the substrate via the
    in-process discovery tests above.
    """
    try:
        result = subprocess.run(
            ["roam", "--version"], capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


@pytest.mark.skipif(not _roam_available(), reason="`roam` console script not on PATH")
def test_subprocess_plugins_list_shows_rails():
    """``roam plugins list`` (subprocess) names the rails plugin."""
    result = subprocess.run(
        ["roam", "plugins", "list"],
        env=_env_with_plugin(),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "rails" in result.stdout, result.stdout
    assert "framework_detection" in result.stdout, result.stdout


@pytest.mark.skipif(not _roam_available(), reason="`roam` console script not on PATH")
def test_subprocess_plugins_info_describes_rails():
    """``roam plugins info rails`` returns the metadata declared in register()."""
    result = subprocess.run(
        ["roam", "plugins", "info", "rails"],
        env=_env_with_plugin(),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "rails" in result.stdout
    assert "0.1.0" in result.stdout


@pytest.mark.skipif(not _roam_available(), reason="`roam` console script not on PATH")
def test_subprocess_plugins_doctor_exits_clean(tmp_path):
    """``roam plugins doctor`` exits 0 with the dogfood plugin loaded."""
    result = subprocess.run(
        ["roam", "plugins", "doctor"],
        env=_env_with_plugin(),
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "all plugins loaded cleanly" in result.stdout
