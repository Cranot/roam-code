"""Show and validate the roam JSON envelope schema."""

from __future__ import annotations

import json as _json

import click

from roam.output.formatter import to_json, json_envelope
from roam.output.schema_registry import get_schema_info, validate_envelope


@click.command("schema")
@click.option("--validate", "validate_file", type=click.Path(exists=True),
              default=None, help="Validate a JSON file against the envelope schema.")
@click.option("--changelog", "show_changelog", is_flag=True,
              help="Show the schema changelog.")
@click.pass_context
def schema_cmd(ctx, validate_file, show_changelog):
    """Show the roam JSON envelope schema and validate output files."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    schema = get_schema_info()

    # --validate FILE mode
    if validate_file:
        _handle_validate(validate_file, schema, json_mode)
        return

    # --changelog mode
    if show_changelog:
        _handle_changelog(schema, json_mode)
        return

    # Default: show schema info
    _handle_info(schema, json_mode)


def _handle_info(schema: dict, json_mode: bool) -> None:
    """Display schema information."""
    if json_mode:
        click.echo(to_json(json_envelope(
            "schema",
            summary={
                "verdict": f"{schema['name']} (version {schema['version']})",
                "schema_name": schema["name"],
                "schema_version": schema["version"],
            },
            schema=schema,
        )))
        return

    click.echo(f"VERDICT: {schema['name']} (version {schema['version']})")
    click.echo()
    click.echo("REQUIRED FIELDS:")
    for field, desc in schema["required_fields"].items():
        click.echo(f"  {field:20s} {desc}")
    click.echo()
    click.echo("CHANGELOG:")
    for entry in schema["changelog"]:
        changes = "; ".join(entry["changes"])
        click.echo(f"  {entry['version']} ({entry['date']}): {changes}")


def _handle_changelog(schema: dict, json_mode: bool) -> None:
    """Display schema changelog."""
    if json_mode:
        click.echo(to_json(json_envelope(
            "schema",
            summary={
                "verdict": f"{schema['name']} changelog ({len(schema['changelog'])} entries)",
                "schema_name": schema["name"],
                "schema_version": schema["version"],
            },
            changelog=schema["changelog"],
        )))
        return

    click.echo(f"VERDICT: {schema['name']} changelog ({len(schema['changelog'])} entries)")
    click.echo()
    click.echo("CHANGELOG:")
    for entry in schema["changelog"]:
        changes = "; ".join(entry["changes"])
        click.echo(f"  {entry['version']} ({entry['date']}): {changes}")


def _handle_validate(filepath: str, schema: dict, json_mode: bool) -> None:
    """Validate a JSON file against the envelope schema."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = _json.load(f)
    except _json.JSONDecodeError as e:
        if json_mode:
            click.echo(to_json(json_envelope(
                "schema",
                summary={
                    "verdict": "invalid JSON file",
                    "schema_name": schema["name"],
                    "schema_version": schema["version"],
                    "is_valid": False,
                },
                validation={"errors": [f"JSON parse error: {e}"]},
            )))
        else:
            click.echo(f"VERDICT: invalid JSON file")
            click.echo(f"  Error: {e}")
        return

    is_valid, errors = validate_envelope(data)

    if json_mode:
        validation_info = {
            "is_valid": is_valid,
            "errors": errors,
            "fields_present": [f for f in schema["required_fields"] if f in data],
            "fields_missing": [f for f in schema["required_fields"] if f not in data],
        }
        if "schema_version" in data:
            validation_info["detected_version"] = data["schema_version"]
        if "command" in data:
            validation_info["detected_command"] = data["command"]
        click.echo(to_json(json_envelope(
            "schema",
            summary={
                "verdict": "valid roam-envelope-v1 output" if is_valid else "invalid roam-envelope-v1 output",
                "schema_name": schema["name"],
                "schema_version": schema["version"],
                "is_valid": is_valid,
            },
            validation=validation_info,
        )))
        return

    if is_valid:
        click.echo("VERDICT: valid roam-envelope-v1 output")
        click.echo()
        if "schema_version" in data:
            click.echo(f"  schema_version: {data['schema_version']}")
        if "command" in data:
            click.echo(f"  command: {data['command']}")
        field_count = sum(1 for f in schema["required_fields"] if f in data)
        click.echo(f"  All {field_count} required fields present")
    else:
        click.echo("VERDICT: invalid roam-envelope-v1 output")
        click.echo()
        for err in errors:
            click.echo(f"  ERROR: {err}")
