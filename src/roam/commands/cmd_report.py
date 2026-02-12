"""Run compound report presets — multiple commands in one shot."""

import json
import subprocess
import sys
import time

import click

from roam.db.connection import find_project_root
from roam.output.formatter import to_json, json_envelope
from roam.commands.resolve import ensure_index


# ---------------------------------------------------------------------------
# Built-in presets
# ---------------------------------------------------------------------------

PRESETS = {
    "first-contact": {
        "description": "Initial codebase overview — map, health, weather, layers, coupling",
        "sections": [
            {"title": "Map", "command": ["map"]},
            {"title": "Health", "command": ["health"]},
            {"title": "Weather", "command": ["weather"]},
            {"title": "Layers", "command": ["layers"]},
            {"title": "Coupling", "command": ["coupling"]},
        ],
    },
    "security": {
        "description": "Security audit — risk analysis, coverage gaps, secret scan",
        "sections": [
            {"title": "Risk", "command": ["risk", "-n", "20"]},
            {"title": "Coverage Gaps", "command": ["coverage-gaps", "--gate-pattern", "auth|permission|guard"]},
            {"title": "Secret Scan", "command": ["grep", "password|secret|token|api.key", "--source-only", "-n", "30"]},
        ],
    },
    "pre-pr": {
        "description": "Pre-PR checklist — risk, blast radius, coupling check",
        "sections": [
            {"title": "PR Risk", "command": ["pr-risk", "--staged"]},
            {"title": "Blast Radius", "command": ["diff", "--staged"]},
            {"title": "Coupling Check", "command": ["coupling"]},
        ],
    },
    "refactor": {
        "description": "Refactoring analysis — weather, dead code, fan analysis, health",
        "sections": [
            {"title": "Weather", "command": ["weather"]},
            {"title": "Dead Code", "command": ["dead", "--summary"]},
            {"title": "Fan Analysis", "command": ["fan"]},
            {"title": "Health", "command": ["health"]},
        ],
    },
}


def _run_section(section, root):
    """Run a single report section as a subprocess.

    Returns (title, success, output_data, stderr).
    """
    cmd = [sys.executable, "-m", "roam", "--json"] + section["command"]
    try:
        result = subprocess.run(
            cmd, cwd=str(root), capture_output=True, text=True,
            timeout=180, encoding="utf-8", errors="replace",
        )
        output = result.stdout.strip()
        try:
            data = json.loads(output) if output else None
        except json.JSONDecodeError:
            data = {"raw": output}

        return (
            section["title"],
            result.returncode == 0,
            data,
            result.stderr.strip() if result.returncode != 0 else "",
        )
    except subprocess.TimeoutExpired:
        return (section["title"], False, None, "timeout (180s)")
    except Exception as e:
        return (section["title"], False, None, str(e))


def _format_markdown(preset_name, results):
    """Format results as GitHub-compatible markdown."""
    lines = [f"## Roam Report: {preset_name}\n"]

    for title, success, data, stderr in results:
        status = "pass" if success else "FAIL"
        lines.append(f"### {title} [{status}]")

        if not success:
            lines.append(f"\n> Error: {stderr}\n")
            continue

        if data and isinstance(data, dict):
            summary = data.get("summary", {})
            if summary:
                parts = [f"**{k}**: {v}" for k, v in summary.items()]
                lines.append(", ".join(parts))
            lines.append("")
            lines.append("<details><summary>Full output</summary>\n")
            lines.append("```json")
            lines.append(json.dumps(data, indent=2, default=str)[:2000])
            lines.append("```")
            lines.append("</details>\n")
        else:
            lines.append("_(no data)_\n")

    return "\n".join(lines)


@click.command()
@click.argument("preset", required=False, default=None)
@click.option("--list", "list_presets", is_flag=True, help="List available presets")
@click.option("--strict", is_flag=True, help="Exit non-zero if any section fails")
@click.option("--md", "markdown", is_flag=True, help="Output GitHub-compatible markdown")
@click.pass_context
def report(ctx, preset, list_presets, strict, markdown):
    """Run a compound report preset — multiple commands in one shot.

    Built-in presets: first-contact, security, pre-pr, refactor.
    """
    json_mode = ctx.obj.get('json') if ctx.obj else False

    if list_presets:
        if json_mode:
            click.echo(to_json(json_envelope("report",
                summary={"presets": len(PRESETS)},
                presets={name: p["description"] for name, p in PRESETS.items()},
            )))
        else:
            click.echo("=== Available Report Presets ===\n")
            for name, p in PRESETS.items():
                sections = ", ".join(s["title"] for s in p["sections"])
                click.echo(f"  {name:<16s}  {p['description']}")
                click.echo(f"    sections: {sections}")
        return

    if not preset:
        click.echo("Usage: roam report <preset>")
        click.echo("Available presets: " + ", ".join(PRESETS.keys()))
        click.echo("Use --list for details.")
        raise SystemExit(1)

    if preset not in PRESETS:
        click.echo(f"Unknown preset: {preset}")
        click.echo("Available: " + ", ".join(PRESETS.keys()))
        raise SystemExit(1)

    ensure_index()
    root = find_project_root()
    preset_data = PRESETS[preset]
    t0 = time.monotonic()

    results = []
    for section in preset_data["sections"]:
        title, success, data, stderr = _run_section(section, root)
        results.append((title, success, data, stderr))

    elapsed = time.monotonic() - t0
    ok_count = sum(1 for _, s, _, _ in results if s)
    fail_count = sum(1 for _, s, _, _ in results if not s)

    if markdown:
        click.echo(_format_markdown(preset, results))
        if strict and fail_count > 0:
            raise SystemExit(1)
        return

    if json_mode:
        click.echo(to_json(json_envelope("report",
            summary={
                "preset": preset,
                "sections_ok": ok_count,
                "sections_failed": fail_count,
                "elapsed_s": round(elapsed, 1),
            },
            preset=preset,
            sections=[
                {
                    "title": title,
                    "success": success,
                    "data": data,
                    "error": stderr if not success else None,
                }
                for title, success, data, stderr in results
            ],
        )))
        if strict and fail_count > 0:
            raise SystemExit(1)
        return

    # --- Text output ---
    click.echo(f"=== Report: {preset} ({ok_count}/{len(results)} OK, {elapsed:.1f}s) ===\n")

    for title, success, data, stderr in results:
        status = "OK" if success else "FAIL"
        click.echo(f"--- {title} [{status}] ---")

        if not success:
            click.echo(f"  Error: {stderr}")
            click.echo()
            continue

        if data and isinstance(data, dict):
            summary = data.get("summary", {})
            if summary:
                parts = [f"{k}={v}" for k, v in summary.items()]
                click.echo(f"  {', '.join(parts)}")
            else:
                click.echo("  (completed)")
        else:
            click.echo("  (completed)")
        click.echo()

    if strict and fail_count > 0:
        click.echo(f"\nSTRICT: {fail_count} section(s) failed.")
        raise SystemExit(1)
