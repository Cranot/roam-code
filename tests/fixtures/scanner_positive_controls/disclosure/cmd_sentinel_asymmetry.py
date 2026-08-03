"""POSITIVE CONTROL for ``scripts/scan_disclosure_asymmetry.py`` — DO NOT "FIX".

This module is a PLANTED DEFECT, not production code. It is never imported
and never executed; it exists only to be parsed by the disclosure scanner on
every invocation.

``warnings_out`` is rendered into the ``if json_mode:`` branch and nowhere
else, so the text branch reports an unqualified ``VERDICT: ok`` for a run the
JSON envelope admits was degraded. That is the exact W1331 defect shape.

The scanner must report EXACTLY one violation here:

    module   cmd_sentinel_asymmetry.py
    command  sentinel_disclosure_probe
    token    warnings_out
    blind    ["text"]

If it does not, the scanner is BROKEN and every "0 violations" it prints
against the real tree is meaningless — a detector that has stopped matching
and a clean repository produce byte-identical output. Repairing the
disclosure below disables the only check that can tell those two apart.
"""

import click


@click.command()
@click.pass_context
def sentinel_disclosure_probe(ctx):
    warnings_out = []
    json_mode = ctx.obj.get("json")
    if json_mode:
        click.echo(to_json({"warnings_out": warnings_out}))  # noqa: F821
    else:
        click.echo("VERDICT: ok")
