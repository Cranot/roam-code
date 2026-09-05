"""Workspace declarations are scoped, malformed-safe, and fresh per scan."""

from __future__ import annotations

import json

import pytest

from roam.commands import cmd_verify_imports as vi


def _manifest(root, relative, data):
    path = root / relative / "package.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.mark.parametrize("workspaces", [["packages/*"], {"packages": ["packages/*"]}])
def test_member_inherits_root_dev_dependencies_without_sibling_leakage(tmp_path, workspaces):
    _manifest(tmp_path, ".", {"workspaces": workspaces, "devDependencies": {"vitest": "1"}})
    _manifest(tmp_path, "packages/api", {"dependencies": {"api-only": "1"}})
    _manifest(tmp_path, "packages/web", {"dependencies": {"web-only": "1"}})
    assert vi._nearest_js_dependency_packages(str(tmp_path), "packages/api/test/spec.ts") == {
        "api-only",
        "vitest",
    }


@pytest.mark.parametrize("nested_repo", [False, True])
def test_nonmember_or_nested_repository_does_not_inherit_root_deps(tmp_path, nested_repo):
    _manifest(tmp_path, ".", {"workspaces": ["packages/*"], "devDependencies": {"vitest": "1"}})
    relative = "packages/vendor" if nested_repo else "examples/vendor"
    manifest = _manifest(tmp_path, relative, {"dependencies": {"own": "1"}})
    if nested_repo:
        (manifest.parent / ".git").write_text("gitdir: elsewhere", encoding="utf-8")
    assert vi._nearest_js_dependency_packages(str(tmp_path), f"{relative}/src/app.ts") == {"own"}


@pytest.mark.parametrize("data", [[], "invalid", 42, None])
def test_nonobject_manifest_is_not_a_dependency_declaration(tmp_path, data):
    _manifest(tmp_path, ".", data)
    assert vi._nearest_js_dependency_packages(str(tmp_path), "src/app.ts") == frozenset()


@pytest.mark.parametrize("section", [[], ["phantom"], "phantom", 42])
def test_malformed_dependency_section_preserves_valid_sections(tmp_path, section):
    _manifest(tmp_path, ".", {"dependencies": section, "devDependencies": {"valid": "1"}})
    assert vi._dep_section_names({"dependencies": section, "devDependencies": {"valid": "1"}}) == {"valid"}
    assert vi._nearest_js_dependency_packages(str(tmp_path), "src/app.ts") == {"valid"}


def test_workspace_patterns_cannot_escape_project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    _manifest(tmp_path, "outside", {"name": "escaped", "dependencies": {"outside-only": "1"}})
    _manifest(root, ".", {"workspaces": ["../outside", str(tmp_path / "outside")]})
    assert vi._workspace_package_files(str(root), ["../outside", str(tmp_path / "outside")]) == []
    assert vi._declared_js_dependency_packages(str(root)) == frozenset()


def test_repeated_real_scan_refreshes_manifests(project_factory):
    from roam.db.connection import open_db

    root = project_factory(
        {
            "package.json": json.dumps({"workspaces": ["packages/*"], "devDependencies": {"vitest": "1"}}),
            "packages/api/package.json": json.dumps({"dependencies": {"api-only": "1"}}),
            "packages/api/spec.ts": 'import { test } from "vitest";\nimport x from "missing-package";\n',
        }
    )
    with open_db(readonly=True, project_root=root) as conn:
        first = vi.verify_imports_for_connection(conn, str(root))
        unresolved = {row["name"] for row in first["imports"] if row["status"] == "unresolved"}
        assert unresolved == {"missing-package"}
        _manifest(root, ".", {"workspaces": ["packages/*"], "devDependencies": {"missing-package": "1"}})
        second = vi.verify_imports_for_connection(conn, str(root))
        unresolved = {row["name"] for row in second["imports"] if row["status"] == "unresolved"}
        assert unresolved == {"vitest"}
