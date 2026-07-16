"""``roam calc-golden`` — golden-master value oracle for calculations (L4).

Extract real historical ``(inputs -> expected outputs)`` cases from data that
co-stores both (legacy DBF tables, CSV, JSONL), audit which rounding rule best
explains each bucket (reverse-engineering the legacy calc from its own data,
including the residual cases NO rule explains — the multi-path tell), and check
a candidate implementation against the corpus **cent-exact** — via a named
rounding rule or any external runner (one process, JSONL on stdio).

This is characterization testing for money math: the corpus is the exogenous
deterministic oracle (no model calls, no judges). ``check`` exits 5 on any
cent-level breach OR when zero cases were replayed on a non-empty corpus (a
silent oracle is treated as a failing oracle, never a passing one).

SARIF is deliberately NOT emitted: corpus extraction/audit/replay is an oracle
pipeline, not a per-violation static finding stream; results ride the JSON
envelope.

Usage::

    roam calc-golden extract INVO.DBF --inputs "net=NETVALUE,rate=VATCATEGOR" \\
        --expect "vat=VATAMOUNT" --bucket-by rate --out .roam/calc-golden/vat.jsonl
    roam calc-golden audit .roam/calc-golden/vat.jsonl --base net --rate rate \\
        --target vat --rate-map "1=24,2=13,3=6,4=17,5=9,7=0,8=0,0=0"
    roam calc-golden check .roam/calc-golden/vat.jsonl --rule half_up --base net \\
        --rate rate --target vat --rate-map "1=24,..."
    roam calc-golden check corpus.jsonl --runner "php artisan calc:replay"
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.index.golden_calc import (
    RULE_SEMANTICS,
    RULES,
    audit_corpus,
    check_with_rule,
    check_with_runner,
    extract_cases,
    iter_records,
    parse_mapping,
    read_corpus,
    write_corpus,
)
from roam.output.formatter import json_envelope, to_json

_EXIT_GATE = 5


def _parse_rate_map(spec: str) -> dict[str, str] | None:
    if not spec.strip():
        return None
    out: dict[str, str] = {}
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise click.UsageError(f"--rate-map entry {part!r} must be CODE=PERCENT")
        code, pct = part.split("=", 1)
        out[code.strip()] = pct.strip()
    return out


def _parse_where(entries: tuple[str, ...]) -> dict[str, str] | None:
    if not entries:
        return None
    out: dict[str, str] = {}
    for e in entries:
        if "=" not in e:
            raise click.UsageError(f"--where entry {e!r} must be COLUMN=VALUE")
        col, val = e.split("=", 1)
        out[col.strip()] = val.strip()
    return out


@roam_capability(
    name="calc-golden",
    category="health",
    summary="Golden-master value oracle: extract (inputs->outputs) cases from historical data; audit rules; check cent-exact",
    inputs=("extract|audit|check", "source/corpus", "--inputs", "--expect", "--rule", "--runner"),
    outputs=("findings_envelope",),
)
@click.group(name="calc-golden")
def calc_golden() -> None:
    """Golden-master calculation oracle: extract / audit / check."""


@calc_golden.command("extract")
@click.argument("source", type=click.Path(exists=True, dir_okay=False))
@click.option("--inputs", "inputs_spec", required=True, help="Input mapping: case_key=SOURCE_COLUMN,...")
@click.option("--expect", "expect_spec", required=True, help="Expected-output mapping: case_key=SOURCE_COLUMN,...")
@click.option("--bucket-by", default="", help="Comma-list of input case_keys to bucket by (coverage axes).")
@click.option("--where", "where_entries", multiple=True, help="COLUMN=VALUE equality filter (repeatable).")
@click.option("--limit", type=int, default=0, help="Stop after N kept cases (0 = all).")
@click.option("--encoding", default="", help="Source text encoding (default: cp1253 for DBF, utf-8 otherwise).")
@click.option("--out", "out_path", required=True, type=click.Path(dir_okay=False), help="Corpus JSONL output path.")
@click.pass_context
def extract(ctx, source, inputs_spec, expect_spec, bucket_by, where_entries, limit, encoding, out_path):
    """Extract golden cases from SOURCE (.dbf / .csv / .jsonl) into a corpus."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    try:
        case_map = parse_mapping(inputs_spec)
        expect_map = parse_mapping(expect_spec)
        needed = sorted(set(case_map.values()) | set(expect_map.values()) | set((_parse_where(where_entries) or {})))
        records = iter_records(source, encoding=encoding or None)
        # DBF: narrow decoding to the needed columns (2 GB tables) — the reader
        # validates the column names loudly.
        if Path(source).suffix.lower() == ".dbf":
            from roam.index.golden_calc import iter_dbf_records

            records = iter_dbf_records(source, columns=needed, encoding=encoding or "cp1253")
        cases, stats = extract_cases(
            records,
            case_map,
            expect_map,
            bucket_by=[b.strip() for b in bucket_by.split(",") if b.strip()],
            where=_parse_where(where_entries),
            limit=limit,
        )
    except (KeyError, ValueError) as exc:
        verdict = f"extract failed: {exc}"
        if json_mode:
            click.echo(to_json(json_envelope("calc-golden", summary={"verdict": verdict}, error="extract_failed")))
        else:
            click.echo(f"VERDICT: {verdict}")
        ctx.exit(2)
        return
    if stats.kept == 0 and stats.read > 0:
        # the suite's own liveness principle applied to its first stage: an
        # empty corpus from a non-empty source is a mapping/filter defect
        # (wrong column spelling, over-tight --where), never a silent success.
        verdict = (
            f"extract produced 0 cases from {stats.read} records "
            f"({stats.skipped_missing} skipped for missing mapped values) — check --inputs/--expect/--where spellings"
        )
        if json_mode:
            click.echo(to_json(json_envelope("calc-golden", summary={"verdict": verdict}, error="empty_corpus")))
        else:
            click.echo(f"VERDICT: {verdict}")
        ctx.exit(2)
        return
    try:
        write_corpus(cases, out_path)
    except OSError as exc:
        verdict = f"cannot write corpus to {out_path}: {exc}"
        if json_mode:
            click.echo(to_json(json_envelope("calc-golden", summary={"verdict": verdict}, error="write_failed")))
        else:
            click.echo(f"VERDICT: {verdict}")
        ctx.exit(2)
        return
    verdict = f"{stats.kept} cases extracted from {stats.read} records into {out_path} ({len(stats.buckets)} buckets)"
    facts = [
        f"{stats.kept} cases extracted",
        f"{stats.read} records scanned",
        f"{stats.skipped_missing} records skipped (missing mapped values)",
        f"{len(stats.buckets)} buckets",
    ]
    summary = {
        "verdict": verdict,
        "cases": stats.kept,
        "records_read": stats.read,
        "skipped_missing": stats.skipped_missing,
        "buckets": stats.buckets,
        "corpus": str(out_path),
    }
    if json_mode:
        click.echo(
            to_json(json_envelope("calc-golden", budget=token_budget, summary=summary, agent_contract={"facts": facts}))
        )
        return
    click.echo(f"VERDICT: {verdict}")
    for bucket, n in sorted(stats.buckets.items(), key=lambda kv: -kv[1])[:12]:
        click.echo(f"  {bucket}: {n}")


@calc_golden.command("audit")
@click.argument("corpus", type=click.Path(exists=True, dir_okay=False))
@click.option("--base", "base_key", required=True, help="Input case_key holding the calculation base (e.g. net).")
@click.option("--rate", "rate_key", required=True, help="Input case_key holding the rate (percent or code).")
@click.option("--target", "target_key", required=True, help="Expect case_key the rules must reproduce (e.g. vat).")
@click.option("--rate-map", "rate_map_spec", default="", help="CODE=PERCENT,... map when --rate is a code field.")
@click.pass_context
def audit(ctx, corpus, base_key, rate_key, target_key, rate_map_spec):
    """Which rounding rule best explains each bucket? (+ residuals no rule explains)."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    try:
        cases = read_corpus(corpus)
    except (ValueError, KeyError, TypeError) as exc:
        raise click.UsageError(f"malformed corpus {corpus}: {exc}") from exc
    report = audit_corpus(cases, base_key, rate_key, target_key, rate_map=_parse_rate_map(rate_map_spec))
    ev, unex = report["cases_evaluated"], report["unexplained_by_best_rule"]
    verdict = (
        f"{ev} cases evaluated across {len(report['buckets'])} buckets; {unex} unexplained by the best per-bucket rule"
    )
    facts = [f"{ev} cases scanned", f"{unex} residual cases found", f"{len(report['buckets'])} buckets"]
    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "calc-golden",
                    budget=token_budget,
                    summary={"verdict": verdict, "rule_semantics": RULE_SEMANTICS, **report},
                    agent_contract={"facts": facts},
                )
            )
        )
        return
    click.echo(f"VERDICT: {verdict}")
    for b in report["buckets"]:
        click.echo(f"\n  bucket {b['bucket']}: {b['cases']} cases — best {b['best_rule']} ({b['best_match_pct']}%)")
        for rule, pct in sorted(b["rule_match_pct"].items(), key=lambda kv: -kv[1]):
            click.echo(f"    {rule:10s} {pct}%")
        for r in b["residual_examples"]:
            click.echo(
                f"    residual: base={r.get(base_key)} rate={r['rate_pct']}% expected={r['expected']} (half_up gives {r['half_up_prediction']})"
            )


@calc_golden.command("check")
@click.argument("corpus", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--rule",
    type=click.Choice(sorted(RULES)),
    default=None,
    help="Oracle = this rounding rule. NOTE: half_up = ties AWAY FROM ZERO "
    "(calc-inventory label half_away_from_zero) — not JS Math.round's half-toward-+inf.",
)
@click.option("--base", "base_key", default="", help="(rule mode) input case_key for the base.")
@click.option("--rate", "rate_key", default="", help="(rule mode) input case_key for the rate.")
@click.option("--target", "target_key", default="", help="(rule mode) expect case_key to reproduce.")
@click.option("--rate-map", "rate_map_spec", default="", help="(rule mode) CODE=PERCENT,... map.")
@click.option("--runner", default="", help="Oracle = external command; JSONL cases on stdin, JSONL results on stdout.")
@click.option(
    "--tolerance",
    default="0.001",
    show_default=True,
    help="Max |delta| treated as equal. >=0.005 disables tie discrimination "
    "(an unrounded product is always within half a cent of the rounded value).",
)
@click.option(
    "--timeout",
    "runner_timeout",
    type=click.IntRange(min=1),
    default=600,
    show_default=True,
    help="(runner mode) seconds before the runner is killed.",
)
@click.option(
    "--sample", type=click.IntRange(min=0), default=0, help="Deterministic stride-sample N cases (0 = full corpus)."
)
@click.pass_context
def check(ctx, corpus, rule, base_key, rate_key, target_key, rate_map_spec, runner, tolerance, runner_timeout, sample):
    """Replay the corpus against an oracle; exit 5 on any cent-level breach.

    Liveness: zero replayed cases on a non-empty corpus exits 5, and in runner
    mode ANY unanswered case (missing > 0) also exits 5 — a runner that
    silently drops the hard buckets must never read green.
    """
    json_mode = ctx.obj.get("json") if ctx.obj else False
    token_budget = ctx.obj.get("budget", 0) if ctx.obj else 0
    if bool(rule) == bool(runner.strip()):
        raise click.UsageError("exactly one oracle required: --rule ... OR --runner ...")
    try:
        tol = Decimal(tolerance)
    except InvalidOperation as exc:
        raise click.UsageError(f"--tolerance {tolerance!r} is not a decimal number") from exc
    if tol < 0:
        raise click.UsageError("--tolerance must be >= 0")
    try:
        cases = read_corpus(corpus)
    except (ValueError, KeyError, TypeError) as exc:
        raise click.UsageError(f"malformed corpus {corpus}: {exc}") from exc
    if sample and len(cases) > sample:
        stride = -(-len(cases) // sample)  # ceil: spans the whole corpus (no untested tail)
        cases = cases[::stride]
    if rule:
        if not (base_key and rate_key and target_key):
            raise click.UsageError("--rule mode needs --base, --rate and --target")
        result = check_with_rule(
            cases, rule, base_key, rate_key, target_key, rate_map=_parse_rate_map(rate_map_spec), tolerance=tol
        )
        oracle = f"rule:{rule}"
    else:
        import shlex

        result = check_with_runner(cases, shlex.split(runner), tolerance=tol, timeout=runner_timeout)
        oracle = f"runner:{runner}"
    is_runner = bool(runner.strip())
    gate_failed = (
        result.failed > 0
        or (result.replayed == 0 and result.total > 0)
        or (is_runner and result.missing > 0)  # partial oracle = failing oracle
    )
    pass_pct = round(100.0 * result.passed / result.replayed, 2) if result.replayed else 0.0
    verdict = (
        f"{result.passed}/{result.replayed} replayed of {result.total} cases cent-exact ({pass_pct}%) vs {oracle}"
        f"{f'; {result.missing} case(s) never answered' if is_runner and result.missing else ''}"
        f"{'; GATE FAILED' if gate_failed else ''}"
        f"{'; LIVENESS: 0 cases replayed' if result.replayed == 0 and result.total > 0 else ''}"
    )
    facts = [
        f"{result.replayed} cases ran",
        f"{result.failed} failures found",
        f"{result.total} cases scanned",
    ]
    summary = {
        "verdict": verdict,
        "oracle": oracle,
        "total": result.total,
        "replayed": result.replayed,
        "passed": result.passed,
        "failed": result.failed,
        "missing": result.missing,
        "pass_pct": pass_pct,
        "gate_failed": gate_failed,
        "buckets": result.buckets,
        "rule_semantics": RULE_SEMANTICS,
    }
    if json_mode:
        click.echo(
            to_json(
                json_envelope(
                    "calc-golden",
                    budget=token_budget,
                    summary=summary,
                    failures=result.failures,
                    agent_contract={"facts": facts},
                )
            )
        )
        ctx.exit(_EXIT_GATE if gate_failed else 0)
        return
    click.echo(f"VERDICT: {verdict}")
    for bucket, b in sorted(result.buckets.items()):
        flag = "" if b["passed"] == b["replayed"] else "  <- FAILURES"
        click.echo(f"  {bucket}: {b['passed']}/{b['replayed']}{flag}")
    for f in result.failures[:10]:
        click.echo(f"  FAIL id={f['id']} bucket={f['bucket']} {f['deltas']}")
    ctx.exit(_EXIT_GATE if gate_failed else 0)
