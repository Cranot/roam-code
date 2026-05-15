"""Bundled YAML language extractor schemas shipped with the wheel (W664).

Houses the per-language tree-sitter query packs that
``roam.languages.query_engine`` loads at runtime to power the
declarative tier-1 extractors. Today the only resident is
``kotlin.yaml``; more will land as additional languages migrate from
hand-rolled Python extractors to the YAML-schema form documented in
``roam.languages.extractor_schema``.

Why this module file exists -- the W643 / W664 lesson.

Pre-W664 this directory was a namespace subdirectory (no
``__init__.py``) that ``pyproject.toml`` listed in
``[tool.setuptools.package-data]`` as ``roam.languages.extractors``.
The lack of an ``__init__.py`` meant
``importlib.resources.files("roam.languages.extractors")`` returned a
``MultiplexedPath``. Combined with ``as_file`` this extracted a *temp*
copy of the directory that the ``with`` block's ``__exit__`` cleaned
up the moment the resource manager closed -- leaving any caller that
captured the path with a stale handle to a now-deleted tempdir. This
was the W643 incident on ``roam.security.taint_rules``, caught
structurally for this package by the W664 drift-guard
(``tests/test_w664_package_data_init_drift.py``).

Adding this module file converts the directory into a regular
subpackage so ``files()`` returns a real on-disk path and the
wheel-safe ``importlib.resources`` lookup behaves like the existing
W554 ``roam.templates.audit_report`` pattern.

No public Python API lives here -- the file's only job is to make the
directory a regular package so the wheel-safe resource loader resolves
correctly. The YAML extractor files (``*.yaml``) remain the data
surface.
"""
