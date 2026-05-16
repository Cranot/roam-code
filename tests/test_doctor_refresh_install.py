"""Tests for ``_check_installed_binary_matches_source`` — the stale-install advisory.

This advisory check surfaces a common foot-gun: editable source in the
working tree has new commands but the on-PATH ``roam`` binary was built
from an older checkout. Agents then hit ``No such command`` errors at
run-time. The check is intentionally advisory (never blocks) — it exists
so the next ``roam doctor`` run names the divergence and points at the
fix command.

Covers three states:
  * fresh         — paths match → passes silently
  * stale_install — paths diverge → hint mentions ``pip install -e .``
  * no_binary     — ``shutil.which("roam")`` returns None → no crash
"""

from __future__ import annotations

from roam.commands import cmd_doctor

# ---------------------------------------------------------------------------
# fresh — import path and on-PATH binary share a site-packages/roam dir
# ---------------------------------------------------------------------------


def test_check_returns_fresh_when_paths_match(tmp_path, monkeypatch):
    """When the binary's site-packages/roam matches the imported module's
    package dir, the check reports state=fresh and passes silently."""
    # Construct a fake install layout:
    #   <tmp>/venv/Lib/site-packages/roam/__init__.py
    #   <tmp>/venv/Scripts/roam.exe          ← what shutil.which returns
    venv = tmp_path / "venv"
    site_pkg_roam = venv / "Lib" / "site-packages" / "roam"
    site_pkg_roam.mkdir(parents=True)
    init_py = site_pkg_roam / "__init__.py"
    init_py.write_text("# fake roam package\n", encoding="utf-8")

    scripts_dir = venv / "Scripts"
    scripts_dir.mkdir(parents=True)
    binary = scripts_dir / "roam.exe"
    binary.write_text("", encoding="utf-8")

    # Stand up a stub ``roam`` module whose __file__ points into the same
    # site-packages tree the (faked) binary would resolve to.
    import types

    fake_roam = types.ModuleType("roam")
    fake_roam.__file__ = str(init_py)
    monkeypatch.setattr(cmd_doctor, "shutil", _make_shutil(str(binary)))
    monkeypatch.setitem(__import__("sys").modules, "roam", fake_roam)

    result = cmd_doctor._check_installed_binary_matches_source()

    assert result["name"] == "Installed binary"
    assert result["passed"] is True
    assert result["_state"] == "fresh"
    assert "share source tree" in result["detail"]


# ---------------------------------------------------------------------------
# stale_install — paths diverge, hint must mention `pip install -e .`
# ---------------------------------------------------------------------------


def test_check_returns_stale_install_when_paths_diverge(tmp_path, monkeypatch):
    """When the binary's site-packages/roam is a different directory than
    the running import's package dir, the check reports stale_install +
    a hint pointing at ``pip install -e .`` / ``uv tool install -e .``."""
    # Editable source tree (running import points here):
    src_tree = tmp_path / "editable_src" / "roam"
    src_tree.mkdir(parents=True)
    src_init = src_tree / "__init__.py"
    src_init.write_text("# editable roam package\n", encoding="utf-8")

    # Older binary install (shutil.which points here):
    binary_venv = tmp_path / "uv_tool_venv"
    binary_pkg = binary_venv / "Lib" / "site-packages" / "roam"
    binary_pkg.mkdir(parents=True)
    (binary_pkg / "__init__.py").write_text("# old roam\n", encoding="utf-8")
    binary_scripts = binary_venv / "Scripts"
    binary_scripts.mkdir(parents=True)
    binary = binary_scripts / "roam.exe"
    binary.write_text("", encoding="utf-8")

    import types

    fake_roam = types.ModuleType("roam")
    fake_roam.__file__ = str(src_init)
    monkeypatch.setattr(cmd_doctor, "shutil", _make_shutil(str(binary)))
    monkeypatch.setitem(__import__("sys").modules, "roam", fake_roam)

    result = cmd_doctor._check_installed_binary_matches_source()

    assert result["name"] == "Installed binary"
    assert result["passed"] is False
    assert result["_state"] == "stale_install"
    # The actionable hint must name a copy-pasteable fix command.
    assert "pip install -e ." in result["detail"] or "uv tool install -e ." in result["detail"]


# ---------------------------------------------------------------------------
# no_binary — shutil.which returns None, check must not crash
# ---------------------------------------------------------------------------


def test_check_handles_missing_binary_gracefully(tmp_path, monkeypatch):
    """When ``shutil.which("roam")`` returns None (e.g. running via
    ``python -m roam``), the check must return state=no_binary cleanly
    instead of crashing on a ``None`` path."""
    src_tree = tmp_path / "src" / "roam"
    src_tree.mkdir(parents=True)
    src_init = src_tree / "__init__.py"
    src_init.write_text("# roam\n", encoding="utf-8")

    import types

    fake_roam = types.ModuleType("roam")
    fake_roam.__file__ = str(src_init)
    monkeypatch.setattr(cmd_doctor, "shutil", _make_shutil(None))
    monkeypatch.setitem(__import__("sys").modules, "roam", fake_roam)

    result = cmd_doctor._check_installed_binary_matches_source()

    assert result["name"] == "Installed binary"
    # no_binary is advisory-pass — we can't claim staleness with no binary to compare.
    assert result["passed"] is True
    assert result["_state"] == "no_binary"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_shutil(which_returns):
    """Build a stub object that mimics the ``shutil`` module's surface
    used inside ``_check_installed_binary_matches_source`` — namely
    ``shutil.which``. Returning a real module-typed stub keeps any
    incidental ``shutil.<other>`` references in the source intact."""
    import shutil as real_shutil
    import types

    stub = types.ModuleType("shutil_stub")
    stub.which = lambda name: which_returns
    # Re-export everything else from real shutil so the check can still
    # call shutil.copy / shutil.rmtree if it ever needs to.
    for attr in dir(real_shutil):
        if attr == "which" or attr.startswith("_"):
            continue
        setattr(stub, attr, getattr(real_shutil, attr))
    return stub
