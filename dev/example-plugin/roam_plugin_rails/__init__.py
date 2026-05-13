"""Rails framework detector — dogfood validation of the R25 plugin substrate.

This plugin owns the Ruby on Rails framework-detection rule. As of
W28.2 (Path A clean-cut), core no longer ships a built-in Rails
check; ``autodetect_framework_profile`` returns ``None`` for a Rails
project unless this plugin is loaded. Detection logic: a project
counts as Rails when its ``Gemfile`` lists ``gem 'rails'`` or
``gem "rails"``.

Why this plugin exists
======================

W22.5 shipped the typed plugin contract (``RoamPluginContext``,
``register_framework_detector``, ``roam plugins list/info/doctor``).
W25.1 first dogfooded it as Path B (shadow registration — core still
owned detection). W28.2 finished the extraction: detection lives
exclusively here. This proves the substrate is flexible enough to
own framework knowledge end-to-end, not just shadow it.

What stayed in core
===================

Only the *detection rule* moved. The ``rails`` *profile* (the
in-memory I/O allowlist used by N+1 detection — ``includes``,
``preload``, etc.) is still defined in
``src/roam/catalog/detectors.py:_FRAMEWORK_PROFILES`` so the
``--framework rails`` flag keeps working when this plugin is not
loaded. This plugin returns the string ``"rails"`` which then keys
into the profile dict.

How to load
===========

For local development (without ``pip install``)::

    PYTHONPATH=dev/example-plugin \
    ROAM_PLUGIN_MODULES=roam_plugin_rails \
    roam plugins list

The env channel is deliberate — this plugin is *not* declared in
``dev/example-plugin/pyproject.toml`` as an entry-point. That keeps
the "dogfood, not published" semantics and forces consumers (and
test fixtures) to opt in explicitly.

For production, ship a separate ``pyproject.toml`` with::

    [project.entry-points."roam.plugins"]
    rails = "roam_plugin_rails:register"
"""

from __future__ import annotations

from pathlib import Path


def detect_rails(project_root: Path) -> str | None:
    """Return ``"rails"`` when ``project_root/Gemfile`` lists ``gem 'rails'``.

    Owns the rule exclusively as of W28.2 — core's
    ``autodetect_framework_profile`` no longer ships a Rails check.

    Failure modes are silent (return ``None``):

    - missing ``Gemfile``
    - unreadable ``Gemfile`` (permission / encoding)
    - ``Gemfile`` exists but does not declare ``gem 'rails'``

    The detector is intentionally cheap (single file read, no parse)
    so the plugin substrate stays under the <10 ms hot-path budget
    advertised in ``RoamPluginContext.register_framework_detector``.
    """
    gemfile = project_root / "Gemfile"
    try:
        text = gemfile.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if "gem 'rails'" in text or 'gem "rails"' in text:
        return "rails"
    return None


def register(ctx) -> None:
    """Wire the Rails detector into roam at plugin-discovery time.

    ``ctx`` is a :class:`roam.plugins.RoamPluginContext`. We call
    :meth:`declare` so ``roam plugins info rails`` returns useful
    metadata, then :meth:`register_framework_detector` to install the
    actual hook.
    """
    ctx.declare(
        name="rails",
        version="0.1.0",
        description=(
            "Detects Ruby on Rails projects via Gemfile contents. "
            "Dogfood plugin validating the R25 substrate."
        ),
    )
    ctx.register_framework_detector(detect_rails)
