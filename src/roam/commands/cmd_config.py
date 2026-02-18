"""Manage per-project roam configuration (.roam/config.json)."""

from __future__ import annotations

import click

from roam.db.connection import (
    find_project_root,
    get_db_path,
    write_project_config,
    _load_project_config,
)
from roam.output.formatter import to_json, json_envelope


@click.command("config")
@click.option("--set-db-dir", "db_dir", default=None,
              help="Redirect the index DB to this directory (useful for network drives).")
@click.option("--show", is_flag=True, help="Print current configuration.")
@click.pass_context
def config(ctx, db_dir, show):
    """Manage per-project roam configuration (.roam/config.json).

    Use ``--set-db-dir`` to redirect the SQLite database to a local directory
    when your project lives on a network drive or cloud-synced folder (OneDrive,
    Dropbox, etc.) where SQLite cannot open a read-only URI connection:

    \b
      # Windows network drive (M:)
      roam config --set-db-dir "C:\\\\Users\\\\you\\\\.roam-dbs\\\\myproject"
      # Or any local path
      roam config --set-db-dir /tmp/roam/myproject

    The setting is saved to ``.roam/config.json`` and takes precedence over
    the default ``.roam/index.db`` location (but the ``ROAM_DB_DIR`` env-var
    still wins if set).
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    root = find_project_root()
    current = _load_project_config(root)

    if db_dir is not None:
        config_path = write_project_config({"db_dir": db_dir}, root)
        if json_mode:
            click.echo(to_json(json_envelope("config",
                summary={"verdict": "saved", "db_dir": db_dir},
                config_path=str(config_path),
                db_dir=db_dir,
            )))
            return
        click.echo(f"Saved db_dir = {db_dir!r}")
        click.echo(f"Config written to {config_path}")
        click.echo(f"DB will be stored at: {get_db_path(root)}")
        return

    if show or db_dir is None:
        if json_mode:
            click.echo(to_json(json_envelope("config",
                summary={"verdict": "ok"},
                **current,
            )))
            return
        if not current:
            click.echo("No .roam/config.json found (using defaults).")
            click.echo(f"Default DB path: {get_db_path(root)}")
            click.echo("")
            click.echo("Tip: if your project is on a network drive and you see")
            click.echo("  'sqlite3.OperationalError: invalid uri authority'")
            click.echo("  run:  roam config --set-db-dir <local-path>")
        else:
            click.echo(f"Config: {root / '.roam' / 'config.json'}")
            for k, v in current.items():
                click.echo(f"  {k} = {v!r}")
            click.echo(f"Resolved DB path: {get_db_path(root)}")
