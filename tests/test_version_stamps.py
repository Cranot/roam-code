"""ROADMAP A6 / W81 — per-component VERSION stamps for drift detection.

When a bridge, extractor, or detector changes its inference logic, the
rows it produced under the older shape carry stale data. Each component
class advertises a ``VERSION`` class attribute that is stamped onto the
emitted row (``edges.bridge_version``, ``symbols.extractor_version``)
and captured wholesale in the manifest under ``component_versions`` —
giving consumers a single field to diff for drift detection.

These tests enforce the contract surface:

* The ABCs (``LanguageBridge``, ``LanguageExtractor``) declare ``VERSION``.
* The detector version map exists and ships with the documented defaults.
* The schema migrations land the new columns at expected names.
* The manifest writer round-trips ``component_versions`` through SQLite.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# ABC VERSION attributes
# ---------------------------------------------------------------------------


def test_bridge_abc_has_version():
    """``LanguageBridge`` declares a ``VERSION`` class attribute.

    Bumping ``LanguageBridge.VERSION`` (or a subclass override) signals
    to consumers that bridge-emitted edges from a prior index carry
    stale shape. Stamped on every bridge edge via ``edges.bridge_version``.
    """
    from roam.bridges.base import LanguageBridge

    assert hasattr(LanguageBridge, "VERSION"), (
        "LanguageBridge must declare a VERSION class attribute "
        "(Audit A6 / W81). Default is '1.0.0'; subclasses bump when "
        "their resolution algorithm changes."
    )
    assert isinstance(LanguageBridge.VERSION, str)
    assert LanguageBridge.VERSION == "1.0.0"


def test_extractor_abc_has_version():
    """``LanguageExtractor`` declares a ``VERSION`` class attribute.

    Stamped onto every symbol row via ``symbols.extractor_version`` —
    a bump invalidates rows produced by the prior extractor.
    """
    from roam.languages.base import LanguageExtractor

    assert hasattr(LanguageExtractor, "VERSION")
    assert isinstance(LanguageExtractor.VERSION, str)
    assert LanguageExtractor.VERSION == "1.0.0"


def test_concrete_bridge_inherits_or_overrides_version():
    """Every registered bridge has a usable ``VERSION`` (inherited or overridden).

    Both shapes are valid:

    * No override → ``type(bridge).VERSION`` resolves to the ABC's "1.0.0".
    * Override → subclass declared its own value (e.g. "1.1.0").

    What's NOT valid is a bridge subclass that shadows ``VERSION`` with
    a non-string / non-SemVer value — the manifest writer ``str()``s it,
    but a non-string sentinel like None would silently lose drift signal.
    """
    # W1295: test_bridges.py + test_bridges_extended.py have autouse
    # fixtures that call _BRIDGES.clear() for isolation. When they run
    # before this test (CI runs are parallel via pytest-xdist), the
    # registry stays empty because _auto_discover() short-circuits when
    # _BRIDGES is non-empty AND vice-versa — once cleared, the bridge
    # imports already happened so re-import is a no-op. Force-clear then
    # re-import every built-in to guarantee a populated registry.
    from roam.bridges import registry as bridge_registry
    from roam.bridges.registry import _auto_discover, get_bridges

    bridge_registry._BRIDGES.clear()
    import importlib

    for mod_name in (
        "roam.bridges.bridge_salesforce",
        "roam.bridges.bridge_protobuf",
        "roam.bridges.bridge_rest_api",
        "roam.bridges.bridge_template",
        "roam.bridges.bridge_config",
        "roam.bridges.bridge_django",
    ):
        try:
            mod = importlib.import_module(mod_name)
            importlib.reload(mod)  # re-trigger register_bridge() side-effect
        except ImportError:
            pass
    _auto_discover()
    bridges = get_bridges()
    # At least one built-in bridge ships; if the registry is empty
    # something has gone wrong at discovery time.
    assert len(bridges) > 0, "No bridges registered — registry discovery broken?"
    for bridge in bridges:
        version = getattr(type(bridge), "VERSION", None)
        assert version is not None, f"{type(bridge).__name__} has no VERSION"
        assert isinstance(version, str), f"{type(bridge).__name__}.VERSION is {type(version).__name__}, expected str"
        assert version  # non-empty


def test_concrete_extractor_inherits_version():
    """A representative dedicated extractor exposes ``VERSION``.

    The Python extractor is the most-frequently-used dedicated path;
    if its VERSION is missing, the symbol-stamp pipeline is broken.
    """
    from roam.languages.registry import get_extractor

    ext = get_extractor("python")
    version = getattr(type(ext), "VERSION", None)
    assert version is not None
    assert isinstance(version, str)


# ---------------------------------------------------------------------------
# Detector versions module
# ---------------------------------------------------------------------------


def test_detector_versions_module_exists():
    """``roam.catalog.versions`` ships the detector version map (W81).

    Detectors are function-based — there is no ABC to hang ``VERSION``
    on. The version map lives in a dedicated module instead, keeping the
    map separate from the detector source so the manifest writer can
    import it without pulling the detector registry.
    """
    from roam.catalog import versions as v

    assert hasattr(v, "DEFAULT_VERSION")
    assert v.DEFAULT_VERSION == "1.0.0"
    assert hasattr(v, "DETECTOR_VERSION_OVERRIDES")
    assert isinstance(v.DETECTOR_VERSION_OVERRIDES, dict)


def test_detector_version_returns_default_for_unknown():
    """Unknown detectors fall back to ``DEFAULT_VERSION``."""
    from roam.catalog.versions import DEFAULT_VERSION, detector_version

    assert detector_version("never-seen-task-id") == DEFAULT_VERSION


def test_detector_version_returns_override_when_present():
    """Listed detectors return their override version (currently nested-lookup)."""
    from roam.catalog.versions import detector_version

    # nested-lookup was tightened in migration 51 — override registered.
    assert detector_version("nested-lookup") != "1.0.0"
    assert detector_version("nested-lookup") == "1.1.0"


# ---------------------------------------------------------------------------
# Schema migrations
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_conn(tmp_path: Path):
    """Open a fresh roam DB with the schema applied."""
    from roam.db.connection import open_db

    proj = tmp_path / "stamps_proj"
    proj.mkdir()
    with open_db(readonly=False, project_root=proj) as conn:
        yield conn


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_edges_table_has_bridge_version_column(fresh_conn):
    """Migration 53 adds ``edges.bridge_version`` for drift stamping."""
    cols = _column_names(fresh_conn, "edges")
    assert "bridge_version" in cols, "edges.bridge_version column missing — A6 migration 53 didn't apply"


def test_symbols_table_has_extractor_version_column(fresh_conn):
    """Migration 54 adds ``symbols.extractor_version`` for drift stamping."""
    cols = _column_names(fresh_conn, "symbols")
    assert "extractor_version" in cols, "symbols.extractor_version column missing — A6 migration 54 didn't apply"


def test_index_manifest_has_component_versions_column(fresh_conn):
    """Migration 55 adds ``index_manifest.component_versions``."""
    cols = _column_names(fresh_conn, "index_manifest")
    assert "component_versions" in cols, (
        "index_manifest.component_versions column missing — A6 migration 55 didn't apply"
    )


def test_user_version_bumped(fresh_conn):
    """A6 bumps USER_VERSION beyond W82's 14."""
    row = fresh_conn.execute("PRAGMA user_version").fetchone()
    assert int(row[0]) >= 15, f"PRAGMA user_version is {row[0]}, expected >= 15 (A6 bump)"


# ---------------------------------------------------------------------------
# Manifest round-trip
# ---------------------------------------------------------------------------


def test_manifest_includes_component_versions(fresh_conn, tmp_path):
    """The manifest captures ``component_versions`` for drift detection.

    Probes the full pipeline: ``_component_versions`` reads the live
    registries, ``write_manifest`` JSON-encodes into the column, and
    ``latest_manifest`` decodes it back into a dict with the expected
    top-level keys.
    """
    from roam.index.manifest import (
        _component_versions,
        latest_manifest,
        record_indexer_run,
    )

    # Probe directly first — keeps the diagnostic on a registry-empty
    # repo informative rather than buried inside a manifest round-trip.
    cv = _component_versions()
    assert isinstance(cv, dict)
    assert set(cv.keys()) == {"bridges", "detectors", "extractors"}
    # At least one of each surface should be populated on a fresh test
    # env. The built-in bridges + extractors + math detectors all
    # auto-register, so empty maps would be a regression.
    assert cv["bridges"], "no bridges in component_versions map"
    assert cv["detectors"], "no detectors in component_versions map"
    assert cv["extractors"], "no extractors in component_versions map"

    # Persist a manifest row.
    inserted = record_indexer_run(fresh_conn, tmp_path)
    assert inserted is not None and inserted > 0

    read_back = latest_manifest(fresh_conn)
    assert read_back is not None
    assert "component_versions" in read_back
    payload = read_back["component_versions"]
    assert isinstance(payload, dict)
    assert set(payload.keys()) == {"bridges", "detectors", "extractors"}


def test_manifest_diff_surfaces_component_version_changes():
    """A version bump shows up in ``manifest_diff``.

    Drift detection is the whole point: if two manifest rows differ
    only by ``component_versions.detectors.nested-lookup``, the diff
    must surface that field so ``roam doctor`` can recommend a re-index.
    """
    from roam.index.manifest import manifest_diff

    base = {
        "roam_version": "1.0.0",
        "schema_version": 15,
        "parser_versions": {},
        "grammar_versions": None,
        "config_hash": "abc",
        "git_head": None,
        "git_dirty_hash": None,
        "enabled_extras": [],
        "index_profile": "all",
        "component_versions": {
            "bridges": {"laravel": "1.0.0"},
            "detectors": {"nested-lookup": "1.0.0"},
            "extractors": {"python": "1.0.0"},
        },
    }
    bumped = json.loads(json.dumps(base))
    bumped["component_versions"]["detectors"]["nested-lookup"] = "1.1.0"

    diff = manifest_diff(base, bumped)
    assert "component_versions" in diff, "component_versions bump must surface in manifest_diff (drift signal)"
