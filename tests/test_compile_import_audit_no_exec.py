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


def test_import_audit_returns_none_without_import_error_in_task():
    assert _probe_import_audit_for_task("refactor the parser module", None) is None
