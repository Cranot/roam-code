"""roam critique — graph-grounded patch verifier (A.2).

Reads a unified diff (stdin) and runs roam-grounded checks against it:

    git diff | roam critique
    git diff main..HEAD | roam critique --json

The killer signal is *clones-not-edited*: for every changed symbol that
has a persisted clone sibling (see ``roam clones --persist``) outside the
diff, we flag the sibling as a likely missed change. v12.0 ships this
plus a minimal blast-radius caller count; v12.1 wires intent ↔
semantic-diff and dark-matter expectations.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import click

from roam.commands.resolve import ensure_index
from roam.critique.aggregator import aggregate
from roam.critique.checks import (
    check_clones_not_edited,
    check_impact,
    check_intent_alignment,
    find_changed_symbols,
    looks_like_unified_diff,
    parse_diff,
)
from roam.db.connection import open_db
from roam.output.formatter import json_envelope, to_json

# Hot-path → bench command. When a diff touches any of these path
# prefixes, the default critique rules can pass while the change
# materially alters retrieval/scoring/graph algorithms. The hint
# names the bench so the user includes it in their verification
# loop. Order matters: first match wins (most specific first).
_BENCH_RELEVANCE_RULES = [
    (
        ("src/roam/retrieve/", "src/roam/eval/"),
        "pytest tests/test_retrieve_cross_repo.py + roam eval-retrieve --tasks bench/retrieve/roam_self.jsonl",
    ),
    (
        ("src/roam/graph/pagerank.py", "src/roam/graph/clusters.py"),
        "pytest tests/test_personalized_pagerank.py tests/test_fallback_contracts.py",
    ),
    (("src/roam/graph/",), "pytest tests/ -k graph_ -m 'not slow'"),
    (
        ("src/roam/languages/", "src/roam/index/parser.py"),
        "pytest tests/test_languages.py tests/test_extractor_grammar_drift.py",
    ),
    (("src/roam/security/taint",), "pytest tests/test_taint_analysis.py tests/test_taint_classifier.py"),
    (("src/roam/critique/",), "pytest tests/test_critique.py"),
    (
        ("src/roam/commands/cmd_oracle.py", "src/roam/commands/cmd_health.py"),
        "pytest tests/test_oracle.py tests/test_commands_health.py",
    ),
]


def _load_critique_overrides() -> list[tuple[tuple[str, ...], str]]:
    """Load project-local bench-hint overrides from ``.roam-critique.yml``.

    Format (deliberately minimal — no nested PyYAML required)::

        bench_hints:
          - paths: ["src/foo/", "src/bar/"]
            hint: "pytest tests/test_foo.py"

    Overrides are PREPENDED to the built-in rules so project-specific
    hints always match first. Silently returns ``[]`` when the file
    is absent or unparseable — this is a hint, not a gate.
    """
    config_path = Path(".roam-critique.yml")
    if not config_path.exists():
        return []
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return []

    rules: list[tuple[tuple[str, ...], str]] = []
    in_bench = False
    cur_paths: list[str] = []
    cur_hint = ""
    pending = False

    def _flush() -> None:
        nonlocal cur_paths, cur_hint, pending
        if cur_paths and cur_hint:
            rules.append((tuple(cur_paths), cur_hint))
        cur_paths = []
        cur_hint = ""
        pending = False

    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not line.startswith(" ") and stripped.endswith(":"):
            _flush()
            in_bench = stripped[:-1] == "bench_hints"
            continue
        if not in_bench:
            continue
        # Item start: "- paths: [...]" or "- hint: ..."
        if stripped.startswith("- "):
            _flush()
            pending = True
            stripped = stripped[2:].strip()
        if not pending:
            continue
        if stripped.startswith("paths:"):
            val = stripped.split(":", 1)[1].strip()
            if val.startswith("[") and val.endswith("]"):
                inner = val[1:-1]
                cur_paths = [p.strip().strip('"').strip("'") for p in inner.split(",") if p.strip()]
        elif stripped.startswith("hint:"):
            cur_hint = stripped.split(":", 1)[1].strip().strip('"').strip("'")
    _flush()
    return rules


def _bench_relevance_hint(regions, overrides=None) -> str:
    """Return a one-line bench/test suggestion when the diff touches a
    structurally-significant path. ``regions`` is the
    ``critique.checks.ChangedRegion`` list from the diff parser; we
    look at each region's file path and pick the first matching rule.

    Project-local rules from ``.roam-critique.yml`` (loaded via
    :func:`_load_critique_overrides`) are searched before the built-in
    list so they can shadow defaults — this is the v12.12 hook the
    dogfood notes asked for.
    """
    paths = []
    for r in regions:
        path = getattr(r, "file_path", None) or getattr(r, "file", None) or ""
        if path:
            paths.append(path.replace("\\", "/"))
    if not paths:
        return ""
    rules = list(overrides or []) + _BENCH_RELEVANCE_RULES
    for path in paths:
        for prefixes, hint in rules:
            if any(path.startswith(p) or p in path for p in prefixes):
                return hint
    return ""


@click.command()
@click.option(
    "--input",
    "input_path",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Read diff from a file instead of stdin.",
)
@click.option(
    "--high-callers",
    type=int,
    default=10,
    show_default=True,
    help="Direct-caller threshold above which `impact` emits a medium-severity finding.",
)
@click.option(
    "--intent",
    "intent_text",
    type=str,
    default=None,
    help=(
        "PR title or commit subject to check for alignment with the diff's "
        "semantic shape (e.g. 'fix login bug', 'rename UserSession -> "
        "Session'). Falls back to the latest git commit subject if a git "
        "repo is detected and this flag is omitted."
    ),
)
@click.pass_context
def critique(ctx, input_path, high_callers, intent_text):
    """Verify a patch against the indexed graph.

    Pipe a unified diff in via stdin (``git diff | roam critique``) or
    pass a file with ``--input``. The output is a ranked list of
    findings: clone siblings that may need the same change, symbols
    with high blast radius, and (in v12.1) intent / dark-matter checks.

    Returns exit code 5 when at least one *high* severity finding is
    present (mirrors ``cmd_rules`` ``EXIT_GATE_FAILURE``) so CI can
    gate on it.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0

    if input_path:
        with open(input_path, encoding="utf-8") as fh:
            diff_text = fh.read()
    else:
        if sys.stdin.isatty():
            from roam.output.errors import MISSING_REQUIRED_ARG, structured_usage_error

            raise structured_usage_error(
                MISSING_REQUIRED_ARG,
                "no diff on stdin and no --input — pipe `git diff` in or pass --input PATH",
            )
        diff_text = sys.stdin.read()

    from roam.output.errors import EMPTY_INPUT, INVALID_DIFF, structured_usage_error

    if not diff_text.strip():
        raise structured_usage_error(EMPTY_INPUT, "diff is empty")

    if not looks_like_unified_diff(diff_text):
        # Earlier silent failures: shell substitutions that lost the diff,
        # paste-buffer truncation, or wrong-format input. Erroring loudly
        # here keeps "no concerns" from masking a no-op invocation.
        raise structured_usage_error(
            INVALID_DIFF,
            "input is not a recognisable unified diff "
            "(no diff/--- /+++/@@ headers found). Pass `git diff` output verbatim.",
        )

    ensure_index()

    regions = parse_diff(diff_text)

    # Auto-pick up latest commit subject if --intent wasn't passed.
    effective_intent = intent_text
    if effective_intent is None:
        try:
            proc = subprocess.run(
                ["git", "log", "-1", "--pretty=%s"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
            )
            if proc.returncode == 0:
                effective_intent = proc.stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            effective_intent = None

    with open_db(readonly=True) as conn:
        changed_symbols = find_changed_symbols(conn, regions)
        findings = []
        findings.extend(check_clones_not_edited(conn, changed_symbols, regions))
        findings.extend(check_impact(conn, changed_symbols, high_callers=high_callers))
        if effective_intent:
            findings.extend(check_intent_alignment(effective_intent, changed_symbols, regions))

    result = aggregate(findings)

    # Bench-relevance hint (redacted, completed v12.12):
    # when the diff touches files in the retrieve / graph / catalog hot
    # path, the default rule set ("clones not edited", "blast radius")
    # can legitimately say "no concerns" while the change quietly
    # alters the structural-rerank scoring formula. Surfacing the bench
    # command makes the verifier conversation include the one
    # validation that actually exercises the modified code. Loaded
    # before output so it lands in BOTH text and JSON.
    overrides = _load_critique_overrides()
    bench_hint = _bench_relevance_hint(regions, overrides=overrides)

    summary = {
        "verdict": result["verdict"],
        "changed_files": len(regions),
        "changed_symbols": len(changed_symbols),
        "findings": len(result["findings"]),
        "high_severity": result["severity_breakdown"].get("high", 0),
        "intent": effective_intent,
        "bench_hint": bench_hint or None,
    }

    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "critique",
                    summary=summary,
                    budget=token_budget,
                    severity_breakdown=result["severity_breakdown"],
                    findings=result["findings"],
                    top_finding=result["top_finding"],
                    bench_hint=bench_hint,
                    changed_symbols=[
                        {
                            "symbol_id": s.symbol_id,
                            "name": s.name,
                            "qualified_name": s.qualified_name,
                            "kind": s.kind,
                            "file_path": s.file_path,
                            "line_start": s.line_start,
                            "line_end": s.line_end,
                        }
                        for s in changed_symbols
                    ],
                )
            )
        )
    else:
        click.echo(f"VERDICT: {result['verdict']}")
        click.echo()
        click.echo(f"  changed files:   {len(regions)}")
        click.echo(f"  changed symbols: {len(changed_symbols)}")
        if result["findings"]:
            click.echo()
            for f in result["findings"]:
                click.echo(f"[{f['severity'].upper()}] {f['check']} :: {f['title']}")
                for line in f["detail"].splitlines():
                    click.echo(f"    {line}")
                click.echo()

        if bench_hint:
            click.echo()
            click.echo(f"BENCH HINT: {bench_hint}")

    if result["severity_breakdown"].get("high", 0) > 0:
        ctx.exit(5)
