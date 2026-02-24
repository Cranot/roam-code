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
@click.option(
    "--semantic-backend",
    type=click.Choice(["auto", "tfidf", "onnx", "hybrid"], case_sensitive=False),
    default=None,
    help="Semantic search backend mode.",
)
@click.option(
    "--set-onnx-model",
    "onnx_model",
    default=None,
    help="Path to local ONNX model file for semantic search.",
)
@click.option(
    "--set-onnx-tokenizer",
    "onnx_tokenizer",
    default=None,
    help="Path to tokenizer.json matching the ONNX model.",
)
@click.option(
    "--set-onnx-max-length",
    "onnx_max_length",
    default=None,
    type=int,
    help="Max token length for ONNX encoder (16-1024).",
)
@click.option("--exclude", "exclude_pattern", default=None,
              help="Add a glob pattern to the exclude list in .roam/config.json.")
@click.option("--remove-exclude", "remove_pattern", default=None,
              help="Remove a glob pattern from the exclude list in .roam/config.json.")
@click.option("--show", is_flag=True, help="Print current configuration.")
@click.pass_context
def config(
    ctx,
    db_dir,
    semantic_backend,
    onnx_model,
    onnx_tokenizer,
    onnx_max_length,
    exclude_pattern,
    remove_pattern,
    show,
):
    """Manage per-project roam configuration (.roam/config.json).

    Use ``--set-db-dir`` to redirect the SQLite database to a local directory
    when your project lives on a network drive or cloud-synced folder (OneDrive,
    Dropbox, etc.) where SQLite cannot open a read-only URI connection:

    \b
      # Windows network drive (M:)
      roam config --set-db-dir "C:\\\\Users\\\\you\\\\.roam-dbs\\\\myproject"
      # Or any local path
      roam config --set-db-dir /tmp/roam/myproject

    Use ``--exclude`` to add file exclusion patterns:

    \b
      roam config --exclude "*_pb2.py"
      roam config --exclude "generated/**"
      roam config --remove-exclude "*_pb2.py"

    Configure local ONNX semantic search:

    \b
      roam config --semantic-backend onnx
      roam config --set-onnx-model "./models/all-MiniLM-L6-v2.onnx"
      roam config --set-onnx-tokenizer "./models/tokenizer.json"
      roam config --set-onnx-max-length 256

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

    semantic_updates = {}
    if semantic_backend is not None:
        semantic_updates["semantic_backend"] = semantic_backend.lower()
    if onnx_model is not None:
        semantic_updates["onnx_model_path"] = onnx_model
    if onnx_tokenizer is not None:
        semantic_updates["onnx_tokenizer_path"] = onnx_tokenizer
    if onnx_max_length is not None:
        semantic_updates["onnx_max_length"] = max(16, min(int(onnx_max_length), 1024))

    if semantic_updates:
        config_path = write_project_config(semantic_updates, root)
        if json_mode:
            click.echo(to_json(json_envelope("config",
                summary={"verdict": "saved-semantic-settings"},
                config_path=str(config_path),
                **semantic_updates,
            )))
            return
        click.echo("Saved semantic settings:")
        for k, v in semantic_updates.items():
            click.echo(f"  {k} = {v!r}")
        click.echo(f"Config written to {config_path}")
        click.echo("Re-run `roam index` to refresh semantic vectors with new settings.")
        return

    if exclude_pattern is not None:
        existing_excludes = current.get("exclude", [])
        if not isinstance(existing_excludes, list):
            existing_excludes = []
        if exclude_pattern not in existing_excludes:
            existing_excludes.append(exclude_pattern)
        config_path = write_project_config({"exclude": existing_excludes}, root)
        if json_mode:
            click.echo(to_json(json_envelope("config",
                summary={"verdict": "exclude-added", "pattern": exclude_pattern},
                exclude=existing_excludes,
                config_path=str(config_path),
            )))
            return
        click.echo(f"Added exclude pattern: {exclude_pattern!r}")
        click.echo(f"Active config excludes: {existing_excludes}")
        click.echo(f"Config written to {config_path}")
        click.echo("")
        click.echo("Note: re-run `roam index` to re-index without excluded files.")
        return

    if remove_pattern is not None:
        existing_excludes = current.get("exclude", [])
        if not isinstance(existing_excludes, list):
            existing_excludes = []
        if remove_pattern in existing_excludes:
            existing_excludes.remove(remove_pattern)
            config_path = write_project_config({"exclude": existing_excludes}, root)
            if json_mode:
                click.echo(to_json(json_envelope("config",
                    summary={"verdict": "exclude-removed", "pattern": remove_pattern},
                    exclude=existing_excludes,
                    config_path=str(config_path),
                )))
                return
            click.echo(f"Removed exclude pattern: {remove_pattern!r}")
            click.echo(f"Active config excludes: {existing_excludes}")
            click.echo(f"Config written to {config_path}")
        else:
            if json_mode:
                click.echo(to_json(json_envelope("config",
                    summary={"verdict": "not-found", "pattern": remove_pattern},
                    exclude=existing_excludes,
                )))
                return
            click.echo(f"Pattern {remove_pattern!r} not found in exclude list.")
            if existing_excludes:
                click.echo(f"Current excludes: {existing_excludes}")
        return

    if show or (
        db_dir is None
        and semantic_backend is None
        and onnx_model is None
        and onnx_tokenizer is None
        and onnx_max_length is None
        and exclude_pattern is None
        and remove_pattern is None
    ):
        # Load full exclude patterns (roamignore + config + built-in)
        from roam.index.discovery import load_exclude_patterns, _load_roamignore, BUILTIN_GENERATED_PATTERNS
        roamignore_patterns = _load_roamignore(root)
        config_excludes = current.get("exclude", [])
        all_patterns = load_exclude_patterns(root)

        if json_mode:
            click.echo(to_json(json_envelope("config",
                summary={"verdict": "ok"},
                exclude_roamignore=roamignore_patterns,
                exclude_config=config_excludes if isinstance(config_excludes, list) else [],
                exclude_builtin=list(BUILTIN_GENERATED_PATTERNS),
                exclude_all=all_patterns,
                **current,
            )))
            return
        if not current and not roamignore_patterns:
            click.echo("No .roam/config.json found (using defaults).")
            click.echo(f"Default DB path: {get_db_path(root)}")
            click.echo("")
            click.echo("Tip: if your project is on a network drive and you see")
            click.echo("  'sqlite3.OperationalError: invalid uri authority'")
            click.echo("  run:  roam config --set-db-dir <local-path>")
        else:
            click.echo(f"Config: {root / '.roam' / 'config.json'}")
            for k, v in current.items():
                if k != "exclude":
                    click.echo(f"  {k} = {v!r}")
            click.echo(f"Resolved DB path: {get_db_path(root)}")

        # Show exclude patterns
        click.echo("")
        click.echo("Exclude patterns:")
        if roamignore_patterns:
            click.echo(f"  .roamignore ({len(roamignore_patterns)} patterns):")
            for p in roamignore_patterns:
                click.echo(f"    {p}")
        else:
            click.echo("  .roamignore: (none)")
        if isinstance(config_excludes, list) and config_excludes:
            click.echo(f"  config.json ({len(config_excludes)} patterns):")
            for p in config_excludes:
                click.echo(f"    {p}")
        else:
            click.echo("  config.json: (none)")
        click.echo(f"  built-in ({len(BUILTIN_GENERATED_PATTERNS)} patterns):")
        for p in BUILTIN_GENERATED_PATTERNS:
            click.echo(f"    {p}")
        click.echo("")
        click.echo(f"Total active patterns: {len(all_patterns)}")
        click.echo("Content-based detection: files with '// Code generated' or '# Generated by' in first 3 lines")
