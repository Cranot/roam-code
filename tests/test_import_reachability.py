"""Filesystem-only import-specifier reachability tests."""

from __future__ import annotations

from roam.security.import_reachability import ImportSite, scan_import_reachability


def test_python_import_sites_and_distribution_aliases(tmp_path) -> None:
    (tmp_path / "app.py").write_text(
        "import requests\nimport yaml as y\nfrom dotenv import load_dotenv\nimport os\n",
        encoding="utf-8",
    )
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "inner.py").write_text("from . import util\n", encoding="utf-8")

    reachability = scan_import_reachability(tmp_path)

    assert reachability.sites_for("requests") == [ImportSite("app.py", 1, "requests")]
    assert reachability.sites_for("PyYAML")
    assert reachability.sites_for("yaml")
    assert reachability.sites_for("python-dotenv")
    assert reachability.sites_for("os") == []
    assert reachability.sites_for("util") == []


def test_javascript_import_sites_exclude_relative_specifiers(tmp_path) -> None:
    (tmp_path / "app.js").write_text(
        "const _ = require('lodash');\nimport express from 'express';\nimport '@scope/pkg/sub';\nimport('./local');\n",
        encoding="utf-8",
    )

    reachability = scan_import_reachability(tmp_path)

    assert reachability.sites_for("lodash")
    assert reachability.sites_for("express")
    assert reachability.sites_for("@scope/pkg")
    assert reachability.sites_for("./local") == []


def test_python_syntax_error_uses_import_regex_fallback(tmp_path) -> None:
    (tmp_path / "broken.py").write_text("import requests\ndef broken(:\n", encoding="utf-8")

    reachability = scan_import_reachability(tmp_path)

    assert reachability.sites_for("requests") == [ImportSite("broken.py", 1, "requests")]


def test_node_modules_is_ignored(tmp_path) -> None:
    node_modules = tmp_path / "node_modules" / "nested"
    node_modules.mkdir(parents=True)
    (node_modules / "ignored.js").write_text("require('lodash');\n", encoding="utf-8")

    reachability = scan_import_reachability(tmp_path)

    assert reachability.sites_for("lodash") == []
