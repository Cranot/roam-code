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


def test_python_local_module_shadows_distribution_alias(tmp_path) -> None:
    (tmp_path / "jwt.py").write_text("# first-party jwt module\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("import jwt\n", encoding="utf-8")

    reachability = scan_import_reachability(tmp_path)

    assert reachability.sites_for("pyjwt") == []


def test_python_distribution_alias_without_local_shadow_remains_reachable(tmp_path) -> None:
    (tmp_path / "app.py").write_text("import jwt\n", encoding="utf-8")

    reachability = scan_import_reachability(tmp_path)

    assert reachability.sites_for("pyjwt") == [ImportSite("app.py", 1, "jwt")]


def test_python_namespace_import_reaches_all_plausible_distributions(tmp_path) -> None:
    (tmp_path / "app.py").write_text("import crypto\n", encoding="utf-8")
    declared_deps = ["pycryptodome", "pycrypto"]

    reachability = scan_import_reachability(tmp_path)

    assert all(reachability.is_reachable(dep) for dep in declared_deps)


def test_python_legacy_one_to_one_alias_still_resolves_after_tuple_conversion(tmp_path) -> None:
    """The multi-distribution change converted alias values str -> tuple; the
    pre-existing 1:1 entries must keep resolving (a str/tuple regression here
    would silently unmatch every classic alias like yaml -> pyyaml)."""
    (tmp_path / "app.py").write_text("import yaml\n", encoding="utf-8")

    reachability = scan_import_reachability(tmp_path)

    assert reachability.sites_for("pyyaml") == [ImportSite("app.py", 1, "yaml")]
    assert reachability.is_reachable("PyYAML")  # case-insensitive package match


def test_python_mixed_case_local_shadow_suppresses_same_cased_import(tmp_path) -> None:
    """First-party suppression compares raw (case-preserved) names on both
    sides: a local ``Crypto.py`` suppresses ``import Crypto`` (pycryptodome's
    real import casing), while a differently-cased local file does not."""
    (tmp_path / "Crypto.py").write_text("# first-party Crypto module\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("import Crypto\n", encoding="utf-8")

    reachability = scan_import_reachability(tmp_path)

    assert reachability.sites_for("pycryptodome") == []


def test_scan_reports_when_a_per_language_file_cap_is_hit(tmp_path) -> None:
    for index in range(3):
        (tmp_path / f"app{index}.py").write_text(f"import dependency{index}\n", encoding="utf-8")

    reachability = scan_import_reachability(tmp_path, max_files=2)

    assert reachability.truncated is True


def test_scan_reports_complete_below_the_per_language_file_cap(tmp_path) -> None:
    (tmp_path / "app.py").write_text("import requests\n", encoding="utf-8")

    reachability = scan_import_reachability(tmp_path, max_files=2)

    assert reachability.truncated is False


def test_node_modules_is_ignored(tmp_path) -> None:
    node_modules = tmp_path / "node_modules" / "nested"
    node_modules.mkdir(parents=True)
    (node_modules / "ignored.js").write_text("require('lodash');\n", encoding="utf-8")

    reachability = scan_import_reachability(tmp_path)

    assert reachability.sites_for("lodash") == []
