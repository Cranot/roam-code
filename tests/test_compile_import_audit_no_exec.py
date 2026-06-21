"""Regression coverage for the W201 import-audit probe.

The probe resolves a module name captured from the (untrusted) task string.
It MUST resolve the module without executing the leaf module's top-level
code — `import {module}` would run arbitrary code under the repo cwd, so the
probe uses importlib.util.find_spec in an isolated interpreter instead.
"""

from __future__ import annotations

from roam.plan.compiler import _probe_import_audit_for_task


def test_import_audit_resolves_stdlib_module():
    out = _probe_import_audit_for_task("ImportError: No module named json", None)
    assert out is not None
    audit = out["import_audit"]
    assert audit["module"] == "json"
    assert audit["importable"] is True
    assert "json" in audit["details"]


def test_import_audit_reports_missing_module():
    out = _probe_import_audit_for_task(
        "ModuleNotFoundError: No module named nope_xyz_not_real", None
    )
    assert out is not None
    audit = out["import_audit"]
    assert audit["importable"] is False
    assert "pip install nope_xyz_not_real" in audit["suggestion"]


def test_import_audit_does_not_execute_leaf_module_top_level(tmp_path):
    """A module captured from the task must be located, not executed."""
    sentinel = tmp_path / "PWNED"
    evil = tmp_path / "evil_probe_target.py"
    evil.write_text(
        f"open({str(sentinel)!r}, 'w').close()\n", encoding="utf-8"
    )

    out = _probe_import_audit_for_task(
        "ImportError: No module named evil_probe_target", str(tmp_path)
    )

    assert out is not None
    audit = out["import_audit"]
    # The module is found (resolved via find_spec)...
    assert audit["importable"] is True
    assert "evil_probe_target.py" in audit["details"]
    # ...but its top-level code was NOT executed.
    assert not sentinel.exists()


def test_import_audit_does_not_execute_parent_package_for_dotted_name(tmp_path):
    """A DOTTED module captured from the task must not execute its parent
    package's top-level code either.

    find_spec on a dotted name imports each intermediate parent to read its
    __path__ — so resolving `evil_pkg.child` would run `evil_pkg/__init__.py`.
    The probe resolves only the head via find_spec (no parents) and walks the
    remaining parts by filesystem lookup, so neither the leaf nor any parent
    executes.
    """
    pkg_dir = tmp_path / "evil_pkg"
    pkg_dir.mkdir()
    parent_sentinel = tmp_path / "PARENT_PWNED"
    leaf_sentinel = pkg_dir / "LEAF_PWNED"
    (pkg_dir / "__init__.py").write_text(
        f"open({str(parent_sentinel)!r}, 'w').close()\n", encoding="utf-8"
    )
    (pkg_dir / "child.py").write_text(
        f"open({str(leaf_sentinel)!r}, 'w').close()\n", encoding="utf-8"
    )

    out = _probe_import_audit_for_task(
        "ImportError: No module named evil_pkg.child", str(tmp_path)
    )

    assert out is not None
    audit = out["import_audit"]
    # The dotted module is resolved (head + walk)...
    assert audit["importable"] is True
    assert "child.py" in audit["details"]
    # ...but neither the parent package's nor the leaf's top-level code ran.
    assert not parent_sentinel.exists()
    assert not leaf_sentinel.exists()


def test_import_audit_dotted_name_missing_child_reports_failed(tmp_path):
    """When the head package exists but a dotted child does not, the audit
    must report FAILED (not OK) — the filesystem walk must not short-circuit
    on the head alone."""
    pkg_dir = tmp_path / "real_pkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")

    out = _probe_import_audit_for_task(
        "ModuleNotFoundError: No module named real_pkg.nope_child", str(tmp_path)
    )
    assert out is not None
    assert out["import_audit"]["importable"] is False


def test_import_audit_returns_none_without_import_error_in_task():
    assert _probe_import_audit_for_task("refactor the parser module", None) is None
