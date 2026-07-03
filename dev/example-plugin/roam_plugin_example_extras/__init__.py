"""Reference plugin (extras) — demonstrates the remaining 3 plugin hooks.

The base ``roam_plugin_example`` package covers the framework-shaped
hooks (``declare`` / ``register_framework_detector`` /
``register_framework_profile`` / ``register_detector``). This sibling
plugin covers the remaining three:

- :meth:`RoamPluginContext.register_command` — add a ``roam <name>``
  CLI subcommand.
- :meth:`RoamPluginContext.register_bridge` — add a cross-language
  symbol-resolution bridge.
- :meth:`RoamPluginContext.register_language_extractor` — add a
  per-language symbol extractor for a new file extension.

Together with ``roam_plugin_example`` these two plugins demonstrate
ALL 7 hooks the typed :class:`RoamPluginContext` exposes. Plugin
authors building a real ``roam-plugin-*`` package can copy-fork the
relevant chunks rather than discover the contract by reading test
code.

How to load
===========

For local development (no install required)::

    PYTHONPATH=dev/example-plugin \
    ROAM_PLUGIN_MODULES=roam_plugin_example_extras \
    roam plugins list

The env channel is deliberate — this package is not declared as an
entry point in ``dev/example-plugin/pyproject.toml`` so consumers
(and test fixtures) opt in explicitly. Production plugins ship a
``pyproject.toml`` with::

    [project.entry-points."roam.plugins"]
    extras = "roam_plugin_example_extras:register"

All 7 hooks at a glance
=======================

==========================================  ==================================================
Hook                                         Demonstrated in
==========================================  ==================================================
``declare``                                  roam_plugin_example (base)
``register_framework_detector``              roam_plugin_example (base) + roam_plugin_rails
``register_framework_profile``               roam_plugin_example (base)
``register_detector``                        roam_plugin_example (base)
``register_command``                         roam_plugin_example_extras (this package)
``register_bridge``                          roam_plugin_example_extras (this package)
``register_language_extractor``              roam_plugin_example_extras (this package)
==========================================  ==================================================
"""

from __future__ import annotations

from .bridge import ExampleBridge
from .extractor import ExampleExtractor


def register(ctx) -> None:
    """Wire the three remaining hooks into roam at plugin-discovery time.

    Each call below is the shortest faithful demonstration a real
    plugin author can copy-fork. The Click command, bridge, and
    extractor each live in their own module so the import surface
    stays clear; ``register_command`` in particular requires a stable
    ``module_path`` + ``attr_name`` pair because roam imports the
    command lazily on first invocation (mirroring core's
    ``LazyGroup``).
    """
    ctx.declare(
        name="example-extras",
        version="0.1.0",
        description=(
            "Reference plugin demonstrating register_command / register_bridge / register_language_extractor."
        ),
    )

    # 1) register_command — adds ``roam example-greet --name <name>``.
    #    The command itself is a stock Click command living in
    #    ``roam_plugin_example_extras/cli.py``. Roam loads it lazily.
    ctx.register_command(
        name="example-greet",
        module_path="roam_plugin_example_extras.cli",
        attr_name="example_greet",
    )

    # 2) register_bridge — adds a synthetic cross-language bridge.
    #    Real bridges (protobuf, salesforce, config) resolve symbol
    #    references that cross language boundaries.
    ctx.register_bridge(ExampleBridge())

    # 3) register_language_extractor — adds a per-language extractor
    #    keyed on the ``.example`` extension. Real extractors parse a
    #    tree-sitter AST and emit symbol/reference dicts.
    ctx.register_language_extractor(
        "example-lang",
        ExampleExtractor,
        extensions=[".example"],
        grammar_alias="generic",
    )
