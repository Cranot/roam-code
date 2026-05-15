"""Reference roam plugin — demonstrates the register(ctx) protocol.

This package is intentionally minimal. Run::

    pip install -e dev/example-plugin/
    roam plugins list

and you should see this plugin appear in the output.

In a real plugin (``roam-plugin-nextjs``, ``roam-plugin-laravel``, …)
the hooks below would:

- ``detect_framework`` — return a slug like ``"nextjs"`` when it sees
  ``next.config.{js,ts,mjs}`` in the project root.
- ``detect_demo_finding`` — query the SQLite index for framework-
  specific patterns (e.g. unprotected getServerSideProps, missing
  ``revalidate`` on a route).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def detect_framework(project_root: Path) -> str | None:
    """No-op framework detector.

    Real plugins inspect ``project_root`` for framework signals
    (manifest files, lockfile entries, config dirs). Returning
    ``None`` here lets the built-in detection rules win and keeps
    this example a true no-op when installed against any project.
    """
    return None


def detect_demo_finding(_conn: Any) -> list[dict]:
    """Trivial detector — returns one synthetic finding.

    Demonstrates the shape every detector must return so roam's
    catalog pipeline (``roam algo``, ``roam recommend``) can consume
    plugin findings the same way it consumes built-in ones.
    """
    return [
        {
            "task_id": "example-task",
            "detected_way": "naive",
            "suggested_way": "better",
            "symbol_id": None,
            "symbol_name": "example.symbol",
            "kind": "function",
            "location": "example.py:1",
            "confidence": "low",
            "reason": "example plugin detector fired",
        }
    ]


def register(ctx) -> None:
    """Wire the plugin into roam at startup.

    ``ctx`` is a :class:`roam.plugins.RoamPluginContext`. See
    ``src/roam/plugins/registry.py`` for the full method surface.

    Demonstrates :meth:`register_framework_profile` (W123 / Wave28.3)
    — the richer alternative to ``register_framework_detector``. The
    profile bundles the detector together with file_patterns,
    recommended_commands, and conventions so downstream surfaces
    (``roam describe``, ``roam brief``, framework-aware MCP tools) can
    consume framework knowledge from a single declaration. The profile
    also wires ``detect_fn`` into the detector registry under the hood
    so legacy consumers (``autodetect_framework_profile``) keep
    working — plugin authors do not need to call both APIs.
    """
    from roam.plugins import FrameworkProfile

    ctx.declare(
        name="example",
        version="0.1.0",
        description="Reference plugin — demonstrates the register(ctx) protocol.",
    )
    ctx.register_framework_profile(
        FrameworkProfile(
            name="example",
            detect_fn=detect_framework,
            file_patterns=("example.config.js", "examples/**"),
            recommended_commands=("describe", "health", "preflight"),
            conventions={
                "controller": "examples/controllers/*",
                "view": "examples/views/*",
            },
        )
    )
    ctx.register_detector("example-task", "naive", detect_demo_finding)
