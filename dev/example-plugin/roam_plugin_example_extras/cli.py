"""Click command demonstrating ``register_command``.

Lives in a dedicated module because :meth:`RoamPluginContext.register_command`
takes a ``module_path`` + ``attr_name`` pair so roam can lazy-import the
command on first invocation (the same pattern core uses for its
``LazyGroup`` registry). A real plugin would organise its commands
under ``roam_plugin_<name>/commands/cmd_*.py``.
"""

from __future__ import annotations

import click


@click.command(name="example-greet")
@click.option("--name", default="world", help="Greeting target.")
def example_greet(name: str) -> None:
    """Print a verdict-first greeting.

    Trivial command — exercises the full plugin -> CLI -> click path
    end-to-end (entry-point discovery, registry recording, ``LazyGroup``
    lookup, Click invocation). Real plugin commands would call into
    ``roam.db.connection.open_db`` and emit a structured JSON envelope
    via ``roam.output.formatter.json_envelope`` (see the command
    template in CLAUDE.md).
    """
    click.echo(f"VERDICT: greeted {name}")
