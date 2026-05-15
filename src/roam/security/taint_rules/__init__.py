"""Bundled taint-engine rules shipped with the wheel (W643).

Houses the canonical YAML rule packs that ``roam.security.taint_engine``
loads at runtime via ``roam taint`` / ``roam cga --include-taint`` and
the in-process taint reachability surfaces.

Pre-W643 this directory was a namespace subdirectory (no ``__init__.py``)
that ``pyproject.toml`` listed in ``[tool.setuptools.package-data]`` as
``roam.security.taint_rules``. The lack of an ``__init__.py`` meant
``importlib.resources.files("roam.security.taint_rules")`` returned a
``MultiplexedPath``; combined with ``as_file`` that extracted a *temp*
copy of the directory which was cleaned up the moment the ``with``
block exited — leaving callers (``load_rules``) with a stale path
pointing at a now-deleted temp dir. Adding this module file converts
the directory into a regular subpackage so ``files()`` returns a real
on-disk path and the wheel-safe importlib.resources lookup behaves like
the existing W554 ``roam.templates.audit_report`` pattern.

No public Python API lives here — the file's only job is to make the
directory a regular package so the wheel-safe resource loader resolves
correctly. The YAML rule files (``*.yaml``) remain the data surface.
"""
