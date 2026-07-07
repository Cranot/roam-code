"""F9 regression — library public-surface detection.

Cross-library validation: requests' ``HTTPAdapter.init_poolmanager`` /
``cert_verify`` / ``proxy_manager_for`` were flagged dead-SAFE at confidence 80
("no production consumers") — the most-subclassed adapter methods in the Python
ecosystem. Shipping "safely delete" for those to the requests team is
credibility death. A public symbol in a public module of a *distributable
library* cannot be proven dead from internal consumers alone.

These tests pin the pure surface logic + ``detect_library`` on a temp tree that
mirrors requests' src-layout, and confirm ``_dead_action`` caps the verdict.
"""

from __future__ import annotations

from pathlib import Path

from roam.output.library_surface import LibrarySurface, detect_library


def _requests_like(root: Path) -> None:
    (root / "pyproject.toml").write_text(
        "[build-system]\nrequires = ['setuptools']\n\n[project]\nname = 'requests'\nversion = '2.0'\n",
        encoding="utf-8",
    )
    pkg = root / "src" / "requests"
    pkg.mkdir(parents=True)
    # __init__ deliberately does NOT re-export HTTPAdapter — requests exposes it
    # as `requests.adapters.HTTPAdapter` (public submodule), the exact case the
    # naive "__all__ / re-export only" heuristic misses.
    (pkg / "__init__.py").write_text("from .sessions import Session\n", encoding="utf-8")
    (pkg / "adapters.py").write_text(
        "class HTTPAdapter:\n    def init_poolmanager(self):\n        pass\n", encoding="utf-8"
    )
    (pkg / "_internal_utils.py").write_text("def to_native_string(s):\n    return s\n", encoding="utf-8")


def test_detect_requests_library_surface(tmp_path) -> None:
    _requests_like(tmp_path)
    surf = detect_library(tmp_path)
    assert surf.is_library
    assert ("src/requests", "src/") in surf.py_pkg_prefixes

    # The report-killer: public method of a public class in a public module.
    assert surf.is_external_facing("init_poolmanager", "HTTPAdapter.init_poolmanager", "src/requests/adapters.py")
    assert surf.is_external_facing("cert_verify", "HTTPAdapter.cert_verify", "src/requests/adapters.py")
    # A private module (``_internal_utils.py``) is NOT external-facing.
    assert not surf.is_external_facing("to_native_string", "to_native_string", "src/requests/_internal_utils.py")
    # A private symbol name is NOT external-facing even in a public module.
    assert not surf.is_external_facing("_private", "HTTPAdapter._private", "src/requests/adapters.py")
    # A file outside the package (a script) is NOT external-facing.
    assert not surf.is_external_facing("helper", "helper", "scripts/build.py")


def test_non_library_returns_false(tmp_path) -> None:
    # A plain app with no pyproject [project] / package.json is not a library.
    (tmp_path / "app.py").write_text("def main():\n    pass\n", encoding="utf-8")
    surf = detect_library(tmp_path)
    assert not surf.is_library
    assert not surf.is_external_facing("main", "main", "app.py")


def test_js_export_surface(tmp_path) -> None:
    # express-like: index.js redirects to lib/express.js which assigns exports.*
    (tmp_path / "package.json").write_text(
        '{"name": "express", "main": "index.js", "version": "5.0.0"}', encoding="utf-8"
    )
    (tmp_path / "index.js").write_text("module.exports = require('./lib/express');\n", encoding="utf-8")
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "express.js").write_text(
        "exports.json = bodyParser.json\nexports.raw = bodyParser.raw\nexports.text = bodyParser.text\n",
        encoding="utf-8",
    )
    surf = detect_library(tmp_path)
    assert surf.is_library
    assert "raw" in surf.js_export_names and "text" in surf.js_export_names
    assert surf.is_external_facing("raw", None, "lib/express.js")
    assert surf.is_external_facing("text", None, "lib/express.js")
    # A same-named symbol in an unrelated file is not the public export.
    assert not surf.is_external_facing("raw", None, "test/support/helpers.js")


def test_private_flag_defaults_off() -> None:
    # A LibrarySurface with is_library=False short-circuits everything.
    surf = LibrarySurface()
    assert not surf.is_external_facing("anything", "anything", "src/x.py")


def test_dead_action_caps_external_facing(tmp_path, monkeypatch) -> None:
    # End-to-end: with the module surface active, a would-be SAFE-80 public
    # library method downgrades to REVIEW-50.
    import roam.commands.cmd_dead as cd

    _requests_like(tmp_path)
    surf = detect_library(tmp_path)
    monkeypatch.setattr(cd, "_ACTIVE_LIB_SURFACE", surf)

    row = {
        "name": "init_poolmanager",
        "qualified_name": "HTTPAdapter.init_poolmanager",
        "file_path": "src/requests/adapters.py",
        "kind": "method",
        "docstring": None,
    }
    action, conf = cd._dead_action(row, file_imported=True, tested=False)
    assert action == "REVIEW"
    assert conf <= 50

    # A private helper in a private module stays SAFE.
    row_priv = {
        "name": "to_native_string",
        "qualified_name": "to_native_string",
        "file_path": "src/requests/_internal_utils.py",
        "kind": "function",
        "docstring": None,
    }
    action2, _ = cd._dead_action(row_priv, file_imported=True, tested=False)
    assert action2 == "SAFE"
