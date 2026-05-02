"""roam taint — graph-reach taint analysis with OpenVEX justifications.

Ships in 2 weeks (per the v12 brainstorm), not a year. The 80/20 cut
between Semgrep CE (intra-procedural only) and CodeQL Pro (paid full
abstract interpretation): a YAML-rule driven path BFS over the
existing edges table with sanitizer-stop nodes.

Examples
--------

    roam taint
    roam taint --rules-dir src/roam/security/taint_rules
    roam taint --ci   # exit 5 on findings (gateable in CI)
    roam --json taint --max-hops 8
"""

from __future__ import annotations

from pathlib import Path

import click

from roam.commands.resolve import ensure_index
from roam.db.connection import open_db
from roam.output.formatter import json_envelope, to_json
from roam.security.taint_engine import (
    OPENVEX_JUSTIFICATIONS,
    OPENVEX_STATUSES,
    load_rules,
    run_taint,
    vex_justification_for,
)


def _default_rules_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "security" / "taint_rules"


@click.command()
@click.option(
    "--rules-dir",
    "rules_dir",
    type=click.Path(exists=True, file_okay=False),
    default=None,
    help=(
        "Directory of YAML rule files (default: built-in pack at "
        "src/roam/security/taint_rules/). Each file declares one rule "
        "with sources / sinks / sanitizers / cwe / severity."
    ),
)
@click.option(
    "--max-hops",
    type=int,
    default=6,
    show_default=True,
    help="Cap on BFS depth from source → sink. Tune for large graphs.",
)
@click.option(
    "--ci",
    "ci_mode",
    is_flag=True,
    help="Exit 5 on any high-severity finding (CI gate).",
)
@click.option(
    "--rule",
    "rule_filter",
    type=str,
    default=None,
    help="Only run rules whose id contains this substring.",
)
@click.option(
    "--rules-pack",
    "rules_pack",
    type=click.Choice(
        ["sqli", "xss", "ssrf", "path-traversal", "command-injection", "deserialization"],
        case_sensitive=False,
    ),
    default=None,
    help=(
        "Run a single starter pack: sqli, xss, ssrf, path-traversal, "
        "command-injection, or deserialization. Sugar over --rule for "
        "discoverability — listed in MEMORY.md and external docs but "
        "absent from the CLI before the dogfood sprint. Combinable with "
        "--rules-dir to filter inside a custom pack directory."
    ),
)
@click.pass_context
def taint(ctx, rules_dir, max_hops, ci_mode, rule_filter, rules_pack):
    """Reach-analysis from rule sources to sinks over the indexed edges.

    Each finding lists the source, the sink, the path that connects
    them, and a flag indicating whether a sanitizer was on the path.
    Sanitized findings are kept (not dropped) so the attestation layer
    can later cite ``inline_mitigations_already_exist`` per OpenVEX.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    sarif_mode = ctx.obj.get("sarif") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    rules_path = Path(rules_dir) if rules_dir else _default_rules_dir()
    rules = load_rules(rules_path)
    if rules_pack:
        # Pack name → substring matched against rule id (e.g. "sqli"
        # matches "python-sqli", "xss" matches "js-xss").
        pack_match = rules_pack.lower()
        rules = [r for r in rules if pack_match in r.rule_id.lower()]
    if rule_filter:
        rules = [r for r in rules if rule_filter.lower() in r.rule_id.lower()]

    if not rules:
        verdict = f"No rules in {rules_path}"
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "taint",
                        summary={
                            "verdict": verdict,
                            "rules": 0,
                            "findings": 0,
                        },
                        rules_dir=str(rules_path),
                    )
                )
            )
            return
        click.echo(f"VERDICT: {verdict}")
        return

    ensure_index()

    with open_db(readonly=True) as conn:
        findings = run_taint(conn, rules, max_hops=max_hops)

    high_count = sum(1 for f in findings if f.severity == "error")
    medium_count = sum(1 for f in findings if f.severity == "warning")
    sanitized_count = sum(1 for f in findings if f.sanitizer_in_path)

    verdict = (
        f"{len(findings)} finding(s) "
        f"({high_count} error, {medium_count} warning, "
        f"{sanitized_count} sanitized) across {len(rules)} rule(s)"
        if findings
        else f"No taint findings across {len(rules)} rule(s)"
    )

    findings_dump = [
        {
            "rule_id": f.rule_id,
            "severity": f.severity,
            "cwe": f.cwe,
            "source": f.source_symbol,
            "sink": f.sink_symbol,
            "path_length": len(f.path_symbols),
            "path": [{"name": p.get("name"), "file": p.get("file"), "line": p.get("line")} for p in f.path_symbols],
            "sanitizer_in_path": f.sanitizer_in_path,
            "vex_justification": (vex_justification_for(f) if f.sanitizer_in_path else None),
        }
        for f in findings
    ]

    if sarif_mode:
        from roam.output.sarif import taint_to_sarif, write_sarif

        click.echo(write_sarif(taint_to_sarif(findings_dump)))
        if ci_mode and high_count > 0:
            ctx.exit(5)
        return

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "taint",
                    summary={
                        "verdict": verdict,
                        "rules": len(rules),
                        "findings": len(findings),
                        "errors": high_count,
                        "warnings": medium_count,
                        "sanitized": sanitized_count,
                    },
                    budget=token_budget,
                    rules_dir=str(rules_path),
                    rule_ids=[r.rule_id for r in rules],
                    findings=findings_dump,
                    openvex_justification_strings=sorted(OPENVEX_JUSTIFICATIONS),
                    openvex_statuses=sorted(OPENVEX_STATUSES),
                )
            )
        )
        if ci_mode and high_count > 0:
            ctx.exit(5)
        return

    click.echo(f"VERDICT: {verdict}")
    click.echo(f"Rules:   {', '.join(r.rule_id for r in rules)}")
    click.echo()
    for f in findings_dump:
        click.echo(f"[{f['severity'].upper()}] {f['rule_id']} ({f['cwe'] or 'no CWE'})")
        src = f["source"]
        sink = f["sink"]
        click.echo(f"  src: {src.get('name')} at {src.get('file')}:{src.get('line')}")
        click.echo(f"  sink: {sink.get('name')} at {sink.get('file')}:{sink.get('line')}")
        click.echo(f"  path: {f['path_length']} hop(s)")
        if f["sanitizer_in_path"]:
            click.echo(f"  sanitized: yes  (VEX: {f['vex_justification']})")
        click.echo()

    if ci_mode and high_count > 0:
        ctx.exit(5)
