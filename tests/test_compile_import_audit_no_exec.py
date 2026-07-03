"""Regression coverage for the W201 import-audit probe.

The probe resolves a module name captured from the (untrusted) task string.
It MUST resolve the module without executing the leaf module's top-level
code — `import {module}` would run arbitrary code under the repo cwd, so the
probe uses importlib.util.find_spec in an isolated interpreter instead.

It must also NEVER recommend `pip install <name>` with a name drawn from the
task unless that name is already a dependency declared in the project manifest
— otherwise attacker-controlled issue/task text could steer an agent toward an
attacker-chosen public package (dependency confusion / typosquatting).
"""

from __future__ import annotations

from roam.plan.compiler import (
    _declared_dependency_names,
    _is_declared_dependency,
    _normalize_dist_name,
    _probe_import_audit_for_task,
)


def test_import_audit_resolves_stdlib_module():
    out = _probe_import_audit_for_task("ImportError: No module named json", None)
    assert out is not None
    audit = out["import_audit"]
    assert audit["module"] == "json"
    assert audit["importable"] is True
    assert "json" in audit["details"]


def test_import_audit_reports_missing_module_without_install_suggestion():
    """A module captured from the task that is not importable and not a
    declared dependency must NOT yield a `pip install <task-name>` suggestion
    (dependency-confusion guard). With cwd=None there is no manifest to check,
    so the name is treated as unverified."""
    out = _probe_import_audit_for_task("ModuleNotFoundError: No module named nope_xyz_not_real", None)
    assert out is not None
    audit = out["import_audit"]
    assert audit["importable"] is False
    assert "pip install nope_xyz_not_real" not in audit["suggestion"]
    assert audit["suggestion"]  # non-empty: a manifest-check hint


def test_import_audit_no_install_for_undeclared_typosquat(tmp_path):
    """A typosquat name present in the task but absent from the manifest must
    not produce an install suggestion, even when the genuine package IS a
    declared dependency — the prompt-derived name is never blessed."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = ["requests>=2.0"]\n',
        encoding="utf-8",
    )
    out = _probe_import_audit_for_task("ModuleNotFoundError: No module named reuqests", str(tmp_path))
    assert out is not None
    audit = out["import_audit"]
    assert audit["module"] == "reuqests"
    assert audit["importable"] is False
    assert "pip install reuqests" not in audit["suggestion"]
    assert "manifest" in audit["suggestion"]


def test_import_audit_install_only_for_declared_dependency(tmp_path):
    """The genuine declared dependency, when named by the task, DOES earn an
    install suggestion — the project already declares it, so installing it is
    intended, not attacker-steered. Uses a deliberately-uninstalled name so the
    probe reports not-importable regardless of the host environment."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = ["fake_xyz_uninstalled>=1.0"]\n',
        encoding="utf-8",
    )
    out = _probe_import_audit_for_task("ModuleNotFoundError: No module named fake_xyz_uninstalled", str(tmp_path))
    assert out is not None
    audit = out["import_audit"]
    assert audit["module"] == "fake_xyz_uninstalled"
    assert audit["importable"] is False
    assert "pip install fake_xyz_uninstalled" in audit["suggestion"]


def test_import_audit_install_for_declared_dotted_head(tmp_path):
    """A dotted module name whose head component is a declared dependency earns
    the install suggestion; an undeclared head does not. Both heads are
    deliberately-uninstalled so importability is environment-independent."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = ["fake_pkg_qqq>=1.0"]\n',
        encoding="utf-8",
    )
    declared = _probe_import_audit_for_task("ImportError: No module named fake_pkg_qqq.core", str(tmp_path))
    assert declared is not None
    assert "pip install fake_pkg_qqq" in declared["import_audit"]["suggestion"]

    bogus = _probe_import_audit_for_task("ImportError: No module named bogus_pkg.core", str(tmp_path))
    assert bogus is not None
    assert "pip install bogus_pkg" not in bogus["import_audit"]["suggestion"]


def test_is_declared_dependency_reads_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\n'
        'dependencies = ["click>=8.0", "tomli; python_version < \'3.11\'"]\n'
        "[project.optional-dependencies]\n"
        'mcp = ["fastmcp>=2.0"]\n',
        encoding="utf-8",
    )
    names = _declared_dependency_names(str(tmp_path))
    assert "click" in names
    assert "tomli" in names
    assert "fastmcp" in names
    # PEP 503 normalization: underscores/dots collapse to '-'.
    assert _is_declared_dependency("click", str(tmp_path)) is True
    assert _is_declared_dependency("fastmcp", str(tmp_path)) is True
    assert _is_declared_dependency("reuqests", str(tmp_path)) is False
    # No manifest -> unverified (fails safe).
    assert _is_declared_dependency("click", None) is False


def test_is_declared_dependency_reads_requirements_files(tmp_path):
    (tmp_path / "requirements.txt").write_text(
        "# comment\n-r other.txt\nrequests>=2.0\nnumpy  # inline comment\n",
        encoding="utf-8",
    )
    (tmp_path / "requirements-dev.txt").write_text("pytest>=7\n", encoding="utf-8")
    names = _declared_dependency_names(str(tmp_path))
    assert "requests" in names
    assert "numpy" in names
    assert "pytest" in names
    assert _is_declared_dependency("requests", str(tmp_path)) is True


def test_normalize_dist_name_pep503():
    assert _normalize_dist_name("Foo.Bar_Baz") == "foo-bar-baz"
    assert _normalize_dist_name("Jinja2") == "jinja2"


def test_import_audit_does_not_execute_leaf_module_top_level(tmp_path):
    """A module captured from the task must be located, not executed."""
    sentinel = tmp_path / "PWNED"
    evil = tmp_path / "evil_probe_target.py"
    evil.write_text(f"open({str(sentinel)!r}, 'w').close()\n", encoding="utf-8")

    out = _probe_import_audit_for_task("ImportError: No module named evil_probe_target", str(tmp_path))

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
    (pkg_dir / "__init__.py").write_text(f"open({str(parent_sentinel)!r}, 'w').close()\n", encoding="utf-8")
    (pkg_dir / "child.py").write_text(f"open({str(leaf_sentinel)!r}, 'w').close()\n", encoding="utf-8")

    out = _probe_import_audit_for_task("ImportError: No module named evil_pkg.child", str(tmp_path))

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

    out = _probe_import_audit_for_task("ModuleNotFoundError: No module named real_pkg.nope_child", str(tmp_path))
    assert out is not None
    assert out["import_audit"]["importable"] is False


def test_import_audit_returns_none_without_import_error_in_task():
    assert _probe_import_audit_for_task("refactor the parser module", None) is None


def test_import_audit_requires_ok_protocol_not_returncode(monkeypatch):
    """A fake/wrapped interpreter that exits 0 WITHOUT emitting the `OK
    <origin>` protocol line must NOT be reported as importable.

    Regression: the audit previously trusted `returncode == 0` alone, so any
    shim ahead of python3 on PATH (or a wrapper that swallows stdout) could
    fabricate a false successful import.
    """
    from roam.plan import compiler

    class _FakeCompleted:
        returncode = 0
        stdout = ""  # exit 0, but no OK protocol line
        stderr = ""

    monkeypatch.setattr(compiler.subprocess, "run", lambda *a, **k: _FakeCompleted())
    out = compiler._probe_import_audit_for_task("ImportError: No module named anything", None)
    assert out is not None
    assert out["import_audit"]["importable"] is False


def test_import_audit_requires_ok_protocol_garbage_stdout(monkeypatch):
    """Exit 0 with non-protocol stdout (e.g. a wrapper's banner) must also
    be treated as not importable."""
    from roam.plan import compiler

    class _FakeCompleted:
        returncode = 0
        stdout = "the operation completed successfully\n"
        stderr = ""

    monkeypatch.setattr(compiler.subprocess, "run", lambda *a, **k: _FakeCompleted())
    out = compiler._probe_import_audit_for_task("ModuleNotFoundError: No module named x", None)
    assert out is not None
    assert out["import_audit"]["importable"] is False


def test_import_audit_trusts_ok_protocol_line(monkeypatch):
    """Conversely, when stdout DOES follow the `OK <origin>` protocol the
    audit reports importable — the protocol line is the source of truth."""
    from roam.plan import compiler

    class _FakeCompleted:
        returncode = 0
        stdout = "OK /some/path/module.py\n"
        stderr = ""

    monkeypatch.setattr(compiler.subprocess, "run", lambda *a, **k: _FakeCompleted())
    out = compiler._probe_import_audit_for_task("ImportError: No module named module", None)
    assert out is not None
    assert out["import_audit"]["importable"] is True
