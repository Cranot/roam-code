"""NEGATIVE CONTROL for ``scripts/scan_disclosure_asymmetry.py`` — DO NOT BREAK.

The twin of ``cmd_sentinel_asymmetry.py``: the same command, with the text
branch echoing the same ``warnings_out`` markers to stderr. This is the
reference fix template (``cmd_understand.py``'s shape), so the scanner must
report NO violation here.

A detector rewired to "always fire" would satisfy the positive control on its
own. This file is what makes the control two-sided: the pair passes only if
the scanner DISCRIMINATES, firing on the asymmetric module and staying silent
on the symmetric one.
"""

import click


@click.command()
@click.pass_context
def sentinel_symmetric_probe(ctx):
    warnings_out = []
    json_mode = ctx.obj.get("json")
    if json_mode:
        click.echo(to_json({"warnings_out": warnings_out}))  # noqa: F821
    else:
        for marker in warnings_out:
            click.echo(f"# warning: {marker}", err=True)
        click.echo("VERDICT: ok")
