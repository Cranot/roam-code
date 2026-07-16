"""Trace the classifier path + procedure decision + per-probe fire/skip reasons.

Diagnostic answer to "why did this get classified as freeform?".

`roam dispatch-trace "<prompt>"` invokes the same compile pipeline as
`roam compile`, but instead of returning the agent-consumption envelope
it surfaces the dispatch decision tree:

  - classifier winner + confidence + alternatives (rejected procedures)
  - per-probe fire/skip status with latency (from W43 timings)
  - final envelope size in bytes
  - normalised task text (the canonical form used for cache keys)

Reads `.roam/compile-runs.jsonl` when an identical task_hash already
exists there (W43/W52/W58 telemetry); otherwise compiles in-process to
capture decisions live.

SARIF is deliberately NOT emitted: output is a routing-decision trace,
not file-located code findings.

Output formats: ``--json`` (default), text.

Displaces:
  - Agent guessing why the compiler picked freeform_explore
  - Manual re-reading of `.roam/compile-runs.jsonl` to find a specific call
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.output.formatter import json_envelope, to_json

# Counterfactual mutation precondition regexes (LAW 4 anchored, deliberately
# narrow — false negatives are fine; false positives skip a useful rephrase).
#
# W11-shape verb: "where is X", "find X", "show me X", "what defines X".
_CF_W11_VERB_RE = re.compile(r"\b(where\s+is|find|locate|show\s+me|what\s+defines)\b", re.I)
# W12-shape anchor: "top N <noun>" — N may be a digit or a small word.
_CF_W12_ANCHOR_RE = re.compile(r"\btop\s+\d+\b", re.I)
# File path: anything containing a `/` or trailing source-file extension.
_CF_PATH_RE = re.compile(
    r"(?:[\w./-]+/[\w./-]+|\b[\w.-]+\.(?:py|js|ts|tsx|jsx|go|rs|java|rb|php|c|cpp|h))",
    re.I,
)
# Bareword: a token of >=3 ASCII letters with no space-or-paren neighbour
# (used by add_cli_perf_frame to pick the first bareword).
_CF_BAREWORD_RE = re.compile(r"\b([A-Za-z][A-Za-z_]{2,})\b")
# Structural-noun families we drop in mutation 4. First match wins; the
# surrounding whitespace is normalised so a clean rephrasing emerges.
_CF_STRUCTURAL_NOUN_RE = re.compile(
    r"\b(imports|callers|coupled|depends|blast)\b",
    re.I,
)


def _select_mutations_for_prompt(task: str) -> list[tuple[str, str]]:
    """Pick up to 5 mutations adapted to the prompt shape.

    Returns ``[(mutation_label, mutated_task), ...]``. Each prompt shape
    gets a different family of mutations so the writing-coach signal is
    relevant to the actual phrasing, not a one-size-fits-all rephrase
    list. Prompts that match no shape fall through to the legacy
    generic floor (``add_top_prefix`` + ``add_definition_verb``).

    Shapes:
      A) CLI verb hint (``roam <subcmd>`` / ``claude <subcmd>``) →
         performance and recency frames.
      B) File path (extension like ``.py``) → coupling and dependents
         frames anchored to the path.
      C) Vague "tell me about X" → narrow to a trace or top-N shape.
      D) Backticked symbol → where-defined and callers frames.

    A prompt may match multiple shapes; mutations stack and are capped
    at 5 entries (deterministic order: A, B, C, D, then floor).
    """
    mutations: list[tuple[str, str]] = []
    task_lower = task.lower()

    # Shape A: CLI verb hint → frame around `roam/claude <subcmd>`.
    if re.search(r"\b(roam|claude)\s+\w+", task):
        mutations.append(("frame_as_why_slow", f"why is {task} slow"))
        mutations.append(("frame_as_recently_changed", f"what changed in {task}"))

    # Shape B: file path → frame around the file.
    if re.search(r"\b\S+\.\w{1,4}\b", task):
        m = re.search(r"\b(\S+\.\w{1,4})\b", task)
        if m:
            path = m.group(1)
            mutations.append(("frame_as_coupling", f"what files are coupled to {path}"))
            mutations.append(("frame_as_dependents", f"what depends on {path}"))

    # Shape C: vague "tell me about X" → trace + top-N shapes.
    if any(kw in task_lower for kw in ("about", "explain", "describe", "tell me")):
        mutations.append(("frame_as_trace", f"trace {task}"))
        mutations.append(("frame_as_top_n", f"top 5 most-relevant files for {task}"))

    # Shape D: backticked symbol → symbol-graph queries.
    if re.search(r"`[A-Za-z_][A-Za-z0-9_]+`", task):
        m = re.search(r"`([A-Za-z_][A-Za-z0-9_]+)`", task)
        if m:
            sym = m.group(1)
            mutations.append(("frame_as_where_defined", f"where is {sym} defined"))
            mutations.append(("frame_as_callers", f"who calls {sym}"))

    # Floor: always include 1-2 generic mutations when shape coverage is thin.
    if len(mutations) < 3:
        mutations.append(("add_top_prefix", f"top 5 {task}"))
        mutations.append(("add_definition_verb", f"where is {task} defined"))

    return mutations[:5]


def _apply_counterfactual_mutations(prompt: str) -> list[tuple[str, str, bool]]:
    """Return ``[(label, mutated_prompt, applied), ...]`` for the 5 mutations.

    A mutation is ``applied`` when its precondition holds. Skipped
    mutations still appear in the list (so downstream callers can show
    "tried but inapplicable") with the original prompt as a placeholder.
    """
    out: list[tuple[str, str, bool]] = []

    # 1. add_definition_verb — prepend "where is " if no W11-shape verb.
    if not _CF_W11_VERB_RE.search(prompt):
        out.append(("add_definition_verb", f"where is {prompt}", True))
    else:
        out.append(("add_definition_verb", prompt, False))

    # 2. add_top_prefix — prepend "top 5 " if no W12-shape anchor.
    if not _CF_W12_ANCHOR_RE.search(prompt):
        out.append(("add_top_prefix", f"top 5 {prompt}", True))
    else:
        out.append(("add_top_prefix", prompt, False))

    # 3. add_cli_perf_frame — wrap as "why is `roam <token>` slow" if a
    # bareword is present. Use the first bareword that isn't a stopword
    # like "the"/"and" — the regex's >=3-char floor handles that.
    m = _CF_BAREWORD_RE.search(prompt)
    if m is not None:
        token = m.group(1)
        out.append(("add_cli_perf_frame", f"why is `roam {token}` slow", True))
    else:
        out.append(("add_cli_perf_frame", prompt, False))

    # 4. drop_structural_noun — remove first match of structural noun family.
    sn = _CF_STRUCTURAL_NOUN_RE.search(prompt)
    if sn is not None:
        mutated = prompt[: sn.start()] + prompt[sn.end() :]
        mutated = re.sub(r"\s{2,}", " ", mutated).strip()
        out.append(("drop_structural_noun", mutated, True))
    else:
        out.append(("drop_structural_noun", prompt, False))

    # 5. anchor_file — append " in src/roam/cli.py" if no file path present.
    if not _CF_PATH_RE.search(prompt):
        out.append(("anchor_file", f"{prompt} in src/roam/cli.py", True))
    else:
        out.append(("anchor_file", prompt, False))

    return out


def _build_counterfactual_block(prompt: str, baseline_procedure: str) -> tuple[list[dict], dict[str, int], int]:
    """Run the shape-adaptive mutations through ``_classify`` and aggregate alt routes.

    Returns ``(per-mutation records, alt-route counts, distinct-route count)``.
    The mutation set is shape-selected by ``_select_mutations_for_prompt``;
    its size varies between 2 and 5 records depending on which shapes
    fire. The schema (record keys + envelope fields) is unchanged.
    """
    from roam.plan.compiler import _classifier_confidence, _classify

    records: list[dict] = []
    alt_routes: dict[str, int] = {}
    distinct = 0
    for label, mutated in _select_mutations_for_prompt(prompt):
        proc, _rejected = _classify(mutated)
        conf = _classifier_confidence(mutated, proc)
        records.append(
            {
                "label": label,
                "mutated_prompt": mutated,
                "procedure": proc,
                "confidence": conf,
                "applied": True,
            }
        )
        if proc != baseline_procedure:
            distinct += 1
            alt_routes[proc] = alt_routes.get(proc, 0) + 1
    return records, alt_routes, distinct


# W43 timing labels surfaced by `PlanV0.to_envelope` (the L1-probe path).
# Used both to enumerate "expected probe families" for skip-reasoning AND
# to render a stable order in the JSON output.
_KNOWN_PROBE_FAMILIES: tuple[str, ...] = (
    "inner_probe",
    "task_text",
    "backtick_fallback",
    "always_on",
    "l10_symbol_resolution",
)


def _normalize_task(task: str) -> str:
    """The canonical form used for cache keys (mirrors W57.5 canonicalizer).

    Conservative: lowercase, collapse whitespace, strip surrounding quotes
    and trailing punctuation. Reading the compiler's _cache_key would
    couple this command to a private helper; we re-implement the public
    contract instead.
    """
    s = task.strip().strip("\"'`")
    s = " ".join(s.split())
    s = s.rstrip(".?!,;:")
    return s.lower()


def _read_telemetry_match(root: str, task: str) -> dict | None:
    """Return the most recent PRODUCTION telemetry row matching ``task``.

    W43/W52/W58 instrumentation writes one row per `roam compile` call to
    `.roam/compile-runs.jsonl`. If a row already exists for this exact task we
    prefer it (real probe timings). Non-production rows (bench/corpus/trace/
    diff/cache/test — including this command's own past runs) are skipped: a
    trace must not present a benchmark's probe timings as production data.
    """
    from roam.plan.agent_mode import is_non_production

    log_path = Path(root) / ".roam" / "compile-runs.jsonl"
    if not log_path.exists():
        return None
    import hashlib

    target = hashlib.sha256(task.encode("utf-8", "replace")).hexdigest()[:12]
    matches: list[dict] = []
    try:
        with open(log_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("task_hash") == target and not is_non_production(row):
                    matches.append(row)
    except OSError:
        return None
    return matches[-1] if matches else None


def _build_probe_decisions(
    prefetched_keys: list[str],
    timings_ms: dict[str, float] | None,
) -> list[dict]:
    """Derive per-probe fire/skip records.

    A probe family ``fired`` when its timing label is present in the
    W43 timings dict AND took non-trivial work (>0 ms recorded). A
    family with no timing entry didn't run (either skipped by routing
    or the L1 path wasn't taken at all).
    """
    timings_ms = timings_ms or {}
    decisions: list[dict] = []
    for family in _KNOWN_PROBE_FAMILIES:
        latency = timings_ms.get(family)
        if latency is None:
            decisions.append(
                {
                    "family": family,
                    "fired": False,
                    "reason": "section_not_invoked (L1 probe path not taken or probe routed past)",
                    "latency_ms": 0,
                }
            )
            continue
        fired = bool(prefetched_keys) or latency > 0
        reason = "emitted prefetched_facts keys" if fired and prefetched_keys else "section ran but emitted no facts"
        decisions.append(
            {
                "family": family,
                "fired": fired,
                "reason": reason,
                "latency_ms": int(round(float(latency))),
            }
        )
    return decisions


@click.command(name="dispatch-trace")
@click.argument("prompt", type=str)
@click.option(
    "--root",
    default=".",
    show_default=True,
    help="Project root containing .roam/compile-runs.jsonl (for the telemetry fast-path).",
)
@click.option(
    "--counterfactual",
    is_flag=True,
    default=False,
    help="Also classify 5 systematic rephrases of PROMPT and show "
    "which mutations route to a different procedure. Useful "
    "as a writing coach when the classifier picks an "
    "unexpected procedure.",
)
@click.pass_context
@roam_capability(
    name="dispatch-trace",
    category="planning",
    summary="Trace classifier path + procedure decision + per-probe fire/skip reasons for a prompt",
    inputs=("prompt",),
    outputs=("dispatch_trace_envelope",),
    examples=(
        'roam dispatch-trace "why is login slow"',
        'roam --json dispatch-trace "Find files coupled to src/roam/cli.py"',
    ),
    tags=("planning", "compiler", "diagnostic"),
    requires_index=False,
    mcp_expose=False,
)
def dispatch_trace(ctx: click.Context, prompt: str, root: str, counterfactual: bool = False) -> None:
    """Emit the classifier + dispatch decision tree for PROMPT."""
    json_mode = ctx.obj.get("json") if ctx.obj else False

    # Reject obviously-empty prompts (mirrors `roam compile`'s guard so the
    # diagnostic stays consistent).
    stripped = prompt.strip()
    if len(stripped) < 1:
        msg = "empty prompt — pass a freeform sentence"
        if json_mode:
            click.echo(
                to_json(
                    {
                        "schema": "roam-dispatch-trace-error-v1",
                        "summary": {
                            "verdict": "prompt_empty",
                            "partial_success": True,
                            "error": msg,
                        },
                    }
                )
            )
        else:
            click.echo(f"VERDICT: prompt_empty\n  {msg}", err=True)
        ctx.exit(2)
        return

    # Telemetry fast-path: reuse a prior row if this exact task already
    # compiled (saves re-running probes). Falls through on cache miss.
    cwd = os.getcwd()
    telemetry_row = _read_telemetry_match(root, prompt)

    # Always invoke the classifier directly — it's cheap (regex only) and
    # gives us the rejected list + explain dump that telemetry doesn't store.
    from roam.plan.compiler import (
        _classifier_confidence,
        _classify,
        _explain_classifier,
        compile_for_artifact,
        compile_plan,
    )

    procedure, rejected = _classify(prompt)
    conf = _classifier_confidence(prompt, procedure)
    explain = _explain_classifier(prompt)

    # Build alternatives from the explain regex-match dump. Each runner-up
    # gets its own confidence under the assumption it had been the winner.
    alternatives: list[dict] = []
    for name in explain.get("regex_matches", {}).keys():
        if name == procedure:
            continue
        alt_conf = _classifier_confidence(prompt, name)
        alternatives.append({"procedure": name, "confidence": alt_conf})
    alternatives.sort(key=lambda d: -d["confidence"])

    # Probe data: prefer telemetry row when available; otherwise compile
    # in-process to capture decisions. Compile is bounded by the existing
    # 20-second timeouts inside `to_envelope`.
    prefetched_keys: list[str] = []
    timings_ms: dict[str, float] = {}
    art_label = "n/a"
    envelope_bytes = 0
    data_source = "n/a"

    if telemetry_row is not None:
        prefetched_keys = list(telemetry_row.get("prefetched_keys") or [])
        timings_ms = dict(telemetry_row.get("probe_timings_ms") or {})
        art_label = str(telemetry_row.get("art_label") or "n/a")
        envelope_bytes = int(telemetry_row.get("envelope_bytes") or 0)
        data_source = "telemetry"
    else:
        try:
            from roam.plan.agent_mode import MODE_TRACE, agent_mode

            plan = compile_plan(prompt, cwd=cwd)
            with agent_mode(MODE_TRACE):  # stamp trace-tool rows out of the KPIs
                env, art_label = compile_for_artifact(plan, cwd=cwd)
            plan_obj = (env or {}).get("plan") or {}
            prefetched = plan_obj.get("prefetched_facts") or {}
            prefetched_keys = sorted(k for k in prefetched if not k.endswith("_definition"))
            timings_ms = dict(getattr(plan, "_w43_timings_ms", {}) or {})
            try:
                envelope_bytes = len(json.dumps(env, default=str))
            except (TypeError, ValueError):
                envelope_bytes = -1
            data_source = "live"
        except Exception as exc:  # noqa: BLE001 — diagnostic must never crash
            data_source = f"live_error:{type(exc).__name__}"

    probe_decisions = _build_probe_decisions(prefetched_keys, timings_ms)
    fired_count = sum(1 for d in probe_decisions if d["fired"])
    skipped_count = len(probe_decisions) - fired_count
    normalized = _normalize_task(prompt)

    verdict = f"Classified as {procedure} (confidence {conf}); {fired_count} probes fired, {skipped_count} skipped"

    # Counterfactual block (off by default). Mutations are bounded by 5
    # `_classify` calls — each is regex-only, total <5ms in practice.
    counterfactuals: list[dict] = []
    alternative_routes: dict[str, int] = {}
    applied_count = 0
    distinct_count = 0
    if counterfactual:
        counterfactuals, alternative_routes, distinct_count = _build_counterfactual_block(prompt, procedure)
        applied_count = sum(1 for c in counterfactuals if c["applied"])
        verdict = f"Routed to {procedure}; {distinct_count} of {applied_count} rephrases route differently"

    if json_mode:
        facts = [
            f"Classifier picked {procedure} on {len(explain.get('regex_matches') or {})} regex matches",
            f"Classifier rejected {len(rejected)} alternatives",
            f"Probes fired: {fired_count} of {len(_KNOWN_PROBE_FAMILIES)} families",
            f"Skipped {skipped_count} probe families",
            f"Envelope serialized to {envelope_bytes} bytes",
        ]
        if counterfactual:
            # LAW 4: terminal token "routes" is in the formatter anchor set.
            facts.append(f"{distinct_count} of {applied_count} rephrases hit alternative routes")

        extras: dict = {
            "probe_decisions": probe_decisions,
            "final_envelope_size_bytes": envelope_bytes,
            "task_text_normalized": normalized,
        }
        summary = {
            "verdict": verdict,
            "procedure": procedure,
            "classifier_confidence": conf,
            "probes_fired": fired_count,
            "probes_skipped": skipped_count,
            "artifact_type": art_label,
            "data_source": data_source,
            "partial_success": data_source.startswith("live_error"),
        }
        if counterfactual:
            extras["counterfactuals"] = counterfactuals
            extras["alternative_routes"] = alternative_routes
            summary["counterfactual_distinct_routes"] = distinct_count
            summary["counterfactual_applied"] = applied_count

        envelope = json_envelope(
            "dispatch-trace",
            summary=summary,
            agent_contract={
                # LAW 4: each fact's terminal token is a concrete plural anchor.
                "facts": facts,
                "next_commands": [
                    f"roam compile {json.dumps(prompt)} --explain",
                    "roam compile-stats --slow-probes",
                ],
                "risks": [],
                "confidence": conf,
            },
            classifier={
                "procedure": procedure,
                "confidence": conf,
                "alternatives": alternatives,
                "rejected": list(rejected),
                "regex_matches": explain.get("regex_matches") or {},
                "named_paths_extracted": explain.get("named_paths_extracted") or [],
                "tiebreak_rules": list(explain.get("tiebreak_rules") or []),
            },
            **extras,
        )
        click.echo(to_json(envelope))
        return

    # Text mode
    click.echo(f"VERDICT: {verdict}")
    click.echo(f"prompt:              {prompt[:200]}")
    click.echo(f"normalized:          {normalized[:200]}")
    click.echo(f"procedure:           {procedure}")
    click.echo(f"confidence:          {conf}")
    click.echo(f"artifact_type:       {art_label}")
    click.echo(f"data_source:         {data_source}")
    click.echo(f"envelope_size_bytes: {envelope_bytes}")
    click.echo("")
    click.echo("classifier alternatives:")
    if not alternatives:
        click.echo("  (none — only the winning procedure matched)")
    for alt in alternatives:
        click.echo(f"  {alt['procedure']:<24s} conf={alt['confidence']}")
    click.echo("")
    click.echo("rejected procedures (with reason):")
    if not rejected:
        click.echo("  (none)")
    for r in rejected:
        click.echo(f"  - {r}")
    click.echo("")
    click.echo("probe decisions:")
    click.echo(f"  {'family':<24s} {'fired':>5s} {'lat_ms':>8s}  reason")
    for d in probe_decisions:
        marker = "yes" if d["fired"] else "no"
        click.echo(f"  {d['family']:<24s} {marker:>5s} {d['latency_ms']:>8d}  {d['reason']}")

    if counterfactual:
        click.echo("")
        click.echo(f"counterfactual rephrases ({distinct_count} of {applied_count} hit alternative routes):")
        click.echo(f"  {'label':<22s} {'applied':>7s}  {'procedure':<24s} conf  mutated_prompt")
        for c in counterfactuals:
            applied = "yes" if c["applied"] else "no"
            proc_s = str(c["procedure"] or "-")
            conf_s = f"{c['confidence']:.2f}" if c["applied"] else "    "
            click.echo(f"  {c['label']:<22s} {applied:>7s}  {proc_s:<24s} {conf_s}  {c['mutated_prompt'][:120]}")
        if alternative_routes:
            click.echo("")
            click.echo("alternative route aggregation:")
            for proc_name, cnt in sorted(alternative_routes.items(), key=lambda kv: -kv[1]):
                click.echo(f"  {proc_name:<32s} {cnt:>3d}")
