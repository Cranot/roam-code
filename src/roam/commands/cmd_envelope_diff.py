"""Compare two `roam compile` envelopes and emit a structured diff.

Two usage shapes:

  1. ``roam envelope-diff "<prompt1>" "<prompt2>"`` — compile both prompts
     in-process and diff the resulting envelopes. Useful for A/B-ing
     prompt-shape changes (does adding "in src/" route the same task to
     a different procedure? did the classifier confidence move?).

  2. ``roam envelope-diff --from-cache <sha1> <sha2>`` — look up two
     envelope rows by their cache `key` in
     ``.roam/compile-envelope-cache.sqlite`` and diff them. Useful for
     comparing what was cached at two points in time (e.g. before/after
     a wave landed) without re-compiling.

The diff reports:
  * added_probes / removed_probes   — keys under `plan.prefetched_facts`
  * changed_probes                  — keys present in both with deltas
  * size_delta_bytes                — len(json(B)) - len(json(A))
  * classifier_delta                — procedure + confidence A vs B

SARIF is deliberately NOT emitted: this is an envelope-shape comparison,
not a file-located finding — no locations[] coordinates exist to populate.

Displaces:
  * Manual ``diff <(roam compile A) <(roam compile B)`` plus jq-spelunking
  * Eyeballing two ``prefetched_facts`` dicts to figure out what changed
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

import click

from roam.capability import roam_capability
from roam.output.formatter import json_envelope, to_json

_CACHE_FILENAME = "compile-envelope-cache.sqlite"

# Regression thresholds (W-baseline regression-contract). Keep these
# stable — CI baselines that survive the wave's first deploy assume
# these constants are part of the contract.
_PROBE_FIRE_RATE_DROP_THRESHOLD = 0.10  # >10pp absolute drop = regression
_CLASSIFIER_CONFIDENCE_DROP_THRESHOLD = 0.10  # >0.1 abs drop = regression


def _cache_path(root: str) -> Path:
    return Path(root) / ".roam" / _CACHE_FILENAME


def _load_envelope_from_cache(root: str, key: str) -> dict | None:
    """Look up an envelope row by its cache `key` (SHA-prefix or full).

    Accepts a prefix match (LIKE 'key%') so callers can pass a short SHA
    the way `git` does. Returns the parsed envelope dict, or None if no
    row matched or the JSON failed to parse.
    """
    path = _cache_path(root)
    if not path.exists():
        return None
    conn = sqlite3.connect(str(path), timeout=1.0)
    try:
        row = conn.execute(
            "SELECT envelope_json FROM env_cache WHERE key=? OR key LIKE ? LIMIT 1",
            (key, f"{key}%"),
        ).fetchone()
    except sqlite3.DatabaseError:
        return None
    finally:
        conn.close()
    if not row or not row[0]:
        return None
    try:
        return json.loads(row[0])
    except (TypeError, ValueError):
        return None


def _prefetched_facts(env: dict) -> dict:
    """Pull the `plan.prefetched_facts` sub-dict (the probe payload) from
    a compile envelope. Tolerates the two common envelope shapes:

      * compile's outer envelope wrapping `artifact.plan.prefetched_facts`
      * a raw artifact dict with `plan.prefetched_facts` at top level
    """
    if not isinstance(env, dict):
        return {}
    # Outer compile envelope wraps the artifact under "artifact"
    art = env.get("artifact") if isinstance(env.get("artifact"), dict) else env
    plan = art.get("plan") if isinstance(art, dict) else None
    if not isinstance(plan, dict):
        return {}
    pf = plan.get("prefetched_facts")
    return pf if isinstance(pf, dict) else {}


def _classifier_signal(env: dict) -> tuple[str, float]:
    """Extract (procedure, classifier_confidence) from an envelope.

    Looks first at summary (compile's outer envelope) then at
    artifact.plan (raw artifact). Returns ("", 0.0) if neither is present.
    """
    if not isinstance(env, dict):
        return "", 0.0
    summary = env.get("summary") if isinstance(env.get("summary"), dict) else {}
    proc = summary.get("procedure") or ""
    conf = summary.get("classifier_confidence")
    if not proc or conf is None:
        art = env.get("artifact") if isinstance(env.get("artifact"), dict) else env
        plan = art.get("plan") if isinstance(art, dict) else None
        if isinstance(plan, dict):
            proc = proc or plan.get("procedure") or ""
            if conf is None:
                conf = plan.get("classifier_confidence")
    try:
        conf_f = float(conf) if conf is not None else 0.0
    except (TypeError, ValueError):
        conf_f = 0.0
    return str(proc or ""), conf_f


def _probe_size_bytes(value) -> int:
    """Serialized size of a single probe's value, in bytes."""
    try:
        return len(json.dumps(value, sort_keys=True, default=str))
    except (TypeError, ValueError):
        return 0


def _changed_probe_fields(va, vb) -> tuple[list[str], list[str]]:
    """For two same-named probes, return (fields_added, fields_removed).

    "Fields" only makes sense when both sides are dicts. For non-dict
    probe shapes (lists, scalars), the lists are empty — the size_delta
    and the bare presence are still meaningful.
    """
    if not (isinstance(va, dict) and isinstance(vb, dict)):
        return [], []
    keys_a = set(va.keys())
    keys_b = set(vb.keys())
    return sorted(keys_b - keys_a), sorted(keys_a - keys_b)


def _diff_envelopes(env_a: dict, env_b: dict) -> dict:
    """Compute the structured diff between two envelopes.

    Returned dict matches the shape consumed by `envelope_diff` for the
    JSON envelope payload (added_probes / removed_probes / changed_probes
    / size_delta_bytes / classifier_delta).
    """
    pf_a = _prefetched_facts(env_a)
    pf_b = _prefetched_facts(env_b)
    keys_a = set(pf_a.keys())
    keys_b = set(pf_b.keys())
    added = sorted(keys_b - keys_a)
    removed = sorted(keys_a - keys_b)
    shared = sorted(keys_a & keys_b)

    changed = []
    for name in shared:
        va = pf_a[name]
        vb = pf_b[name]
        size_a = _probe_size_bytes(va)
        size_b = _probe_size_bytes(vb)
        if va == vb:
            continue
        fa, fr = _changed_probe_fields(va, vb)
        changed.append(
            {
                "name": name,
                "size_delta_bytes": size_b - size_a,
                "fields_added": fa,
                "fields_removed": fr,
            }
        )

    try:
        size_a_total = len(json.dumps(env_a, sort_keys=True, default=str))
    except (TypeError, ValueError):
        size_a_total = 0
    try:
        size_b_total = len(json.dumps(env_b, sort_keys=True, default=str))
    except (TypeError, ValueError):
        size_b_total = 0

    proc_a, conf_a = _classifier_signal(env_a)
    proc_b, conf_b = _classifier_signal(env_b)

    return {
        "added_probes": added,
        "removed_probes": removed,
        "changed_probes": changed,
        "size_delta_bytes": size_b_total - size_a_total,
        "classifier_delta": {
            "procedure_a": proc_a,
            "procedure_b": proc_b,
            "confidence_a": round(conf_a, 4),
            "confidence_b": round(conf_b, 4),
        },
    }


def _task_hash(prompt: str) -> str:
    """Stable 12-char identifier for a baseline prompt.

    Matches the existing telemetry hashing convention used elsewhere
    (sha256(prompt) truncated to 12 hex chars). The truncation gives a
    short, filesystem-friendly directory name with negligible collision
    risk at the per-repo scale of an envelope-baselines corpus.
    """
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]


def _baseline_paths(baseline_dir: str, prompt: str) -> tuple[Path, Path, Path]:
    """Resolve (task_root, envelope_path, meta_path) for a prompt under
    ``baseline_dir``. The task_root is ``<DIR>/<task_hash>/``; the
    envelope lives at ``envelope.json`` and the meta at
    ``baseline_meta.json`` underneath it.
    """
    task_root = Path(baseline_dir) / _task_hash(prompt)
    return task_root, task_root / "envelope.json", task_root / "baseline_meta.json"


def _load_baseline(baseline_dir: str, prompt: str) -> tuple[dict | None, dict | None]:
    """Load (envelope, meta) for ``prompt`` from ``baseline_dir``.

    Returns (None, None) when the baseline directory or either file is
    missing or unreadable. Meta may be ``{}`` if the file exists but is
    empty / malformed — that is treated as "envelope present, meta
    absent" rather than a hard miss so callers can still diff.
    """
    _root, env_path, meta_path = _baseline_paths(baseline_dir, prompt)
    if not env_path.exists():
        return None, None
    try:
        envelope = json.loads(env_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    meta: dict = {}
    if meta_path.exists():
        try:
            loaded = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                meta = loaded
        except (OSError, ValueError):
            meta = {}
    return envelope, meta


def _detect_repo_head(cwd: str) -> str:
    """Best-effort HEAD SHA. Returns ``""`` when not a git repo / no git
    binary. Used to stamp baseline_meta.head at write time."""
    try:
        head_file = Path(cwd) / ".git" / "HEAD"
        if not head_file.exists():
            return ""
        ref = head_file.read_text(encoding="utf-8").strip()
        if ref.startswith("ref: "):
            ref_path = Path(cwd) / ".git" / ref[5:].strip()
            if ref_path.exists():
                return ref_path.read_text(encoding="utf-8").strip()[:12]
            return ""
        return ref[:12]
    except OSError:
        return ""


def _classifier_version_for(envelope: dict) -> str:
    """Pull a classifier_version stamp from an envelope (best-effort).

    Looks at summary.classifier_version, then artifact.plan
    .classifier_version, then envelope.version, finally ``""``.
    """
    if not isinstance(envelope, dict):
        return ""
    summary = envelope.get("summary") if isinstance(envelope.get("summary"), dict) else {}
    v = summary.get("classifier_version") if isinstance(summary, dict) else None
    if v:
        return str(v)
    art = envelope.get("artifact") if isinstance(envelope.get("artifact"), dict) else envelope
    plan = art.get("plan") if isinstance(art, dict) else None
    if isinstance(plan, dict):
        v = plan.get("classifier_version")
        if v:
            return str(v)
    v = envelope.get("version")
    return str(v) if v else ""


def _write_baseline(baseline_dir: str, prompt: str, envelope: dict, cwd: str) -> tuple[Path, dict]:
    """Write the envelope + baseline_meta.json for ``prompt`` under
    ``baseline_dir``. Creates parent directories as needed (mkdir -p).
    Returns (task_root, written_meta).
    """
    task_root, env_path, meta_path = _baseline_paths(baseline_dir, prompt)
    task_root.mkdir(parents=True, exist_ok=True)
    env_path.write_text(json.dumps(envelope, sort_keys=True, indent=2, default=str), encoding="utf-8")
    meta = {
        "created_at": int(time.time()),
        "head": _detect_repo_head(cwd),
        "classifier_version": _classifier_version_for(envelope),
        "task_prefix": prompt[:80],
    }
    meta_path.write_text(json.dumps(meta, sort_keys=True, indent=2, default=str), encoding="utf-8")
    return task_root, meta


def _probe_fire_rate(envelope: dict) -> float:
    """Fraction of probe families with non-empty value, in [0.0, 1.0].

    A probe family is "fired" when its value is truthy AND non-empty
    (dict / list / non-empty string / non-zero number). An envelope
    with no prefetched_facts at all has fire_rate 0.0. This matches the
    compile-pipeline's own internal `probe_fire_rate` metric semantics.
    """
    pf = _prefetched_facts(envelope)
    if not pf:
        return 0.0
    fired = 0
    for value in pf.values():
        if value is None:
            continue
        if isinstance(value, (dict, list, str)) and len(value) == 0:
            continue
        fired += 1
    return fired / len(pf) if pf else 0.0


# Artifact-type quality rank for the downgrade rule. `l1_probe` (probe-and-fill,
# answers embedded) is the headline KPI; anything below it is a weaker envelope
# for the agent. Unknown/absent types default to the base rank (no false
# downgrade against a baseline that predates artifact_type stamping).
_ARTIFACT_RANK = {"l1_probe": 3, "facts": 2, "lean": 2, "contract": 1, "full": 1}


def _is_empty_value(v) -> bool:
    """True for a fired-but-empty probe payload ([] / {} / "")."""
    return isinstance(v, (dict, list, str)) and len(v) == 0


def _artifact_type(env: dict) -> str:
    """Pull `summary.artifact_type` from an outer compile envelope or the inner
    artifact dict. "" when absent (older baselines)."""
    summary = env.get("summary") if isinstance(env.get("summary"), dict) else {}
    at = summary.get("artifact_type") or ""
    if not at:
        art = env.get("artifact") if isinstance(env.get("artifact"), dict) else env
        if isinstance(art, dict):
            at = (art.get("summary") or {}).get("artifact_type") or ""
    return at or ""


# Rule 7-9 thresholds (partial-degradation + erosion + broad-collapse edge cases).
_CONTENT_SHRINK_THRESHOLD = 0.50  # a core family collection lost >50% of entries
_SHRINK_MIN_BASELINE = 3  # ...and had >=3 baseline entries (ignore tiny collections)
_LOW_CONF_FLOOR = 0.60  # the low-confidence routing boundary
_ENVELOPE_SIZE_COLLAPSE_THRESHOLD = 0.40  # total prefetched bytes dropped >40%
_SIZE_MIN_BASELINE = 500  # ...on a non-trivial envelope (ignore tiny ones)


def _collection_len(v) -> int:
    """Entry count of a probe value (list/dict); 0 for scalars/None."""
    return len(v) if isinstance(v, (list, dict)) else 0


def _total_pf_bytes(env: dict) -> int:
    """Total serialized bytes of all prefetched_facts probe payloads."""
    return sum(_probe_size_bytes(v) for v in _prefetched_facts(env).values())


# 2026-06-02 dogfood: some always_on EXTENDER probes spawn subprocesses
# (ripgrep) and fire non-deterministically under concurrent execution
# (grep_replication measured ~15% miss). They are BONUS context, not the
# procedure's core answer. Excluding them from the family-level rules keeps the
# regression contract from flaking while still catching a real regression (a
# CORE probe like stack_frames / callers / coupling dropping). See LOOPS memo.
_FLAKY_EXTENDER_FAMILIES = frozenset(
    {
        "grep_results",
        "config_matches",
        "semantic_matches",
        "runtime_hotspots",
    }
)


def _is_real_family(k: str) -> bool:
    """True for a CORE probe family. `_definition` / `_unavailable` keys are
    annotations; flaky extenders fire non-deterministically. Neither counts.

    Underscore-prefixed keys (`_envelope_budget_pruned`,
    `_section_budget_truncated`, `_w32_subprobe_timings_ms`, ...) are internal
    diagnostics, not probe data — their presence depends on whether the
    envelope hit the byte budget, so their DISAPPEARANCE is an improvement
    (smaller envelope), never a probe regression. (2026-06-10: these falsely
    tripped `probe_family_missing` on the stack_trace baseline after the
    envelope shrank under budget.)"""
    if k.startswith("_"):
        return False
    if k.endswith("_definition") or k.endswith("_unavailable"):
        return False
    return k not in _FLAKY_EXTENDER_FAMILIES


def _rule_probe_fire_rate_drop(b: dict, c: dict) -> dict | None:
    """Rule 1 — aggregate probe fire-rate fell past the drop threshold."""
    drop = _probe_fire_rate(b) - _probe_fire_rate(c)
    if drop > _PROBE_FIRE_RATE_DROP_THRESHOLD:
        return {
            "rule": "probe_fire_rate_drop",
            "threshold": _PROBE_FIRE_RATE_DROP_THRESHOLD,
            "actual": round(drop, 4),
            "severity": "high",
        }
    return None


def _rule_classifier_confidence_drop(b: dict, c: dict) -> dict | None:
    """Rule 2 — classifier confidence fell past the drop threshold."""
    drop = _classifier_signal(b)[1] - _classifier_signal(c)[1]
    if drop > _CLASSIFIER_CONFIDENCE_DROP_THRESHOLD:
        return {
            "rule": "classifier_confidence_drop",
            "threshold": _CLASSIFIER_CONFIDENCE_DROP_THRESHOLD,
            "actual": round(drop, 4),
            "severity": "high",
        }
    return None


def _rule_probe_family_missing(b: dict, c: dict) -> dict | None:
    """Rule 3 — a core probe family present in baseline went MISSING."""
    missing = sorted(k for k in (set(_prefetched_facts(b)) - set(_prefetched_facts(c))) if _is_real_family(k))
    if missing:
        return {
            "rule": "probe_family_missing",
            "threshold": 0,
            "actual": len(missing),
            "severity": "high",
            "missing_families": missing,
        }
    return None


def _rule_procedure_reroute(b: dict, c: dict) -> dict | None:
    """Rule 4 — classifier routed to a different procedure (a confident
    misroute that slips past the confidence-drop rule)."""
    proc_b, proc_c = _classifier_signal(b)[0], _classifier_signal(c)[0]
    if proc_b and proc_c and proc_b != proc_c:
        return {
            "rule": "procedure_reroute",
            "severity": "high",
            "baseline_procedure": proc_b,
            "current_procedure": proc_c,
        }
    return None


def _rule_artifact_downgrade(b: dict, c: dict) -> dict | None:
    """Rule 5 — weaker envelope kind (e.g. l1_probe → full). Fires only when
    BOTH carry an artifact_type, so pre-stamping baselines never false-trip."""
    art_b, art_c = _artifact_type(b), _artifact_type(c)
    if art_b and art_c and _ARTIFACT_RANK.get(art_c, 1) < _ARTIFACT_RANK.get(art_b, 1):
        return {
            "rule": "artifact_downgrade",
            "severity": "high",
            "baseline_artifact": art_b,
            "current_artifact": art_c,
        }
    return None


def _rule_core_family_emptied(b: dict, c: dict) -> dict | None:
    """Rule 6 — a family that carried data in baseline now fires EMPTY (a
    resolution/probe degradation the aggregate rate masks)."""
    pf_b, pf_c = _prefetched_facts(b), _prefetched_facts(c)
    emptied = sorted(
        k
        for k in (set(pf_b) & set(pf_c))
        if _is_real_family(k) and not _is_empty_value(pf_b.get(k)) and _is_empty_value(pf_c.get(k))
    )
    if emptied:
        return {
            "rule": "core_family_emptied",
            "severity": "high",
            "actual": len(emptied),
            "emptied_families": emptied,
        }
    return None


def _rule_core_family_content_shrank(b: dict, c: dict) -> dict | None:
    """Rule 7 — present + non-empty in both, but lost > _CONTENT_SHRINK_THRESHOLD
    of its entries (e.g. callers 8 → 2). A partial degradation rules 1/3/6 miss."""
    pf_b, pf_c = _prefetched_facts(b), _prefetched_facts(c)
    shrunk = []
    for k in set(pf_b) & set(pf_c):
        if not _is_real_family(k):
            continue
        nb, nc = _collection_len(pf_b.get(k)), _collection_len(pf_c.get(k))
        if nb >= _SHRINK_MIN_BASELINE and nc < nb * (1 - _CONTENT_SHRINK_THRESHOLD):
            shrunk.append({"family": k, "baseline_n": nb, "current_n": nc})
    if shrunk:
        return {
            "rule": "core_family_content_shrank",
            "severity": "high",
            "actual": len(shrunk),
            "shrunk_families": sorted(shrunk, key=lambda x: x["family"]),
        }
    return None


def _rule_confidence_floor_cross(b: dict, c: dict) -> dict | None:
    """Rule 8 — confidence crossed the low-confidence floor (>=0.6 → <0.6) even
    without a >0.1 absolute drop (slow erosion past the routing threshold)."""
    conf_b, conf_c = _classifier_signal(b)[1], _classifier_signal(c)[1]
    if conf_b >= _LOW_CONF_FLOOR and conf_c < _LOW_CONF_FLOOR:
        return {
            "rule": "confidence_floor_cross",
            "severity": "high",
            "baseline_confidence": round(conf_b, 4),
            "current_confidence": round(conf_c, 4),
            "floor": _LOW_CONF_FLOOR,
        }
    return None


def _rule_envelope_size_collapse(b: dict, c: dict) -> dict | None:
    """Rule 9 — total prefetched bytes dropped > _ENVELOPE_SIZE_COLLAPSE_THRESHOLD
    (broad loss across many probes that no single per-family rule trips)."""
    sb, sc = _total_pf_bytes(b), _total_pf_bytes(c)
    if sb >= _SIZE_MIN_BASELINE and sc < sb * (1 - _ENVELOPE_SIZE_COLLAPSE_THRESHOLD):
        return {
            "rule": "envelope_size_collapse",
            "severity": "high",
            "baseline_bytes": sb,
            "current_bytes": sc,
        }
    return None


# Ordered regression rules. Each is DETERMINISTIC (regex classifier + cached
# index → no LLM, no flake) and emits at most one `high`-severity finding, so a
# single rule fails the gate and the gate is a true behavioral snapshot diff.
_REGRESSION_RULES = (
    _rule_probe_fire_rate_drop,
    _rule_classifier_confidence_drop,
    _rule_probe_family_missing,
    _rule_procedure_reroute,
    _rule_artifact_downgrade,
    _rule_core_family_emptied,
    _rule_core_family_content_shrank,
    _rule_confidence_floor_cross,
    _rule_envelope_size_collapse,
)


def _compute_regression_findings(baseline_env: dict, current_env: dict) -> list[dict]:
    """Apply every regression rule to (baseline, current) and collect findings."""
    return [f for rule in _REGRESSION_RULES if (f := rule(baseline_env, current_env)) is not None]


def _compile_envelope(task: str, cwd: str) -> dict:
    """Compile a freeform task in-process and return the artifact envelope.

    Imported lazily so `roam envelope-diff --from-cache ...` doesn't pay
    the ~200-500ms compile-pipeline import cost.
    """
    # Lazy import — `roam.plan.compiler` is heavy (~5800 lines + networkx).
    from roam.plan.compiler import compile_for_artifact, compile_plan

    plan = compile_plan(task, cwd=cwd)
    env, label = compile_for_artifact(plan, cwd=cwd)
    # 2026-06-02 dogfood fix: the inner artifact env (from
    # `compile_for_artifact`) carries only {plan, schema, schema_version};
    # it has NEITHER `summary.classifier_confidence` NOR
    # `plan.classifier_confidence`. `_classifier_signal` read 0.0 for every
    # fresh compile, producing false-positive "confidence dropped" regression
    # findings. Stamp the real classifier signal + artifact label from the
    # PlanV0 object so `_classifier_signal` / regression checks see truth.
    if isinstance(env, dict):
        summary = env.setdefault("summary", {})
        if isinstance(summary, dict):
            summary.setdefault("procedure", plan.procedure)
            summary.setdefault("classifier_confidence", plan.classifier_confidence)
            summary.setdefault("artifact_type", label)
    return env


def _build_regression_facts(
    findings: list[dict], baseline_meta: dict | None, base_rate: float, cur_rate: float, conf_b: float, conf_c: float
) -> list[str]:
    """LAW 4-anchored facts list for regression mode.

    Every terminal token must appear in
    `roam.output.formatter.concrete_plural_terminals`. Anchors used
    here: ``findings``, ``passed``, ``failed``, ``removed``,
    ``confirmed``, ``checked``.
    """
    missing_count = sum(len(f.get("missing_families", [])) for f in findings if f["rule"] == "probe_family_missing")
    gate_state = "failed" if findings else "passed"
    head = baseline_meta.get("head", "") if isinstance(baseline_meta, dict) else ""
    head_disp = head or "unknown"
    return [
        f"{len(findings)} regression findings",
        f"regression gate {gate_state}",
        f"{missing_count} baseline probe families removed",
        f"probe_fire_rate {base_rate:.2f} -> {cur_rate:.2f} confirmed",
        f"classifier_confidence {conf_b:.2f} -> {conf_c:.2f} confirmed",
        f"baseline head {head_disp} checked",
    ]


def _build_facts(diff: dict) -> list[str]:
    """LAW 4-anchored facts list. Every terminal token must appear in
    `roam.output.formatter.concrete_plural_terminals` so the LAW 4 lint
    passes. See AGENTS.md § LAW 4 for the anchor vocabulary.
    """
    added = diff["added_probes"]
    removed = diff["removed_probes"]
    changed = diff["changed_probes"]
    size_delta = diff["size_delta_bytes"]
    cdelta = diff["classifier_delta"]
    # Every terminal token below MUST appear in
    # `roam.output.formatter.concrete_plural_terminals` (LAW 4). Current
    # anchors used: `added`, `removed`, `matches`, `shifts`, `bytes`.
    return [
        f"{len(added)} probe families added",
        f"{len(removed)} probe families removed",
        f"{len(changed)} probe families with field-shape shifts",
        f"Classifier A→B: {cdelta['procedure_a'] or 'unknown'} → {cdelta['procedure_b'] or 'unknown'} across 2 matches",
        f"Classifier confidence A {cdelta['confidence_a']:.2f} vs B {cdelta['confidence_b']:.2f} across 2 matches",
        f"Envelope size delta {size_delta} bytes",
        f"{len(added) + len(removed) + len(changed)} total probe-family shifts",
    ]


@click.command(name="envelope-diff")
@click.argument("a", type=str)
@click.argument("b", type=str, required=False, default=None)
@click.option(
    "--from-cache",
    "from_cache",
    is_flag=True,
    default=False,
    help="Treat A and B as cache `key` SHAs (or SHA-prefixes); "
    "look them up in .roam/compile-envelope-cache.sqlite "
    "instead of compiling them in-process.",
)
@click.option(
    "--root",
    default=".",
    show_default=True,
    help="Repo root (used to locate the envelope cache and as cwd for in-process compile).",
)
@click.option(
    "--baseline",
    "baseline_dir",
    default=None,
    help="Compare envelope A (compile of prompt) against the matching baseline under <DIR>/<task_hash>/. CI gate.",
)
@click.option(
    "--update-baseline",
    "update_baseline_dir",
    default=None,
    help="Recompile prompt A and OVERWRITE the matching baseline "
    "envelope + meta under <DIR>/<task_hash>/. Skips diffing.",
)
@click.option(
    "--regression",
    is_flag=True,
    default=False,
    help="With --baseline, exit 5 on regression: probe_fire_rate "
    "drop >10%, classifier_confidence drop >0.1, or a "
    "baseline probe family went missing in current.",
)
@click.pass_context
@roam_capability(
    name="envelope-diff",
    category="planning",
    summary="Structured diff between two compile envelopes — probes added / removed / changed plus classifier delta.",
    inputs=("a", "b", "--from-cache", "--root", "--baseline", "--update-baseline", "--regression"),
    outputs=("diff_envelope",),
    examples=(
        'roam envelope-diff "find files coupled to src/cli.py" "who calls handleSave"',
        "roam envelope-diff --from-cache abc123 def456",
        'roam envelope-diff "trace login" --baseline internal/benchmarks/envelope-baselines/ --regression',
        'roam envelope-diff "trace login" --update-baseline internal/benchmarks/envelope-baselines/',
    ),
    tags=("planning", "compiler", "diff"),
)
def envelope_diff(
    ctx: click.Context,
    a: str,
    b: str | None,
    from_cache: bool,
    root: str,
    baseline_dir: str | None,
    update_baseline_dir: str | None,
    regression: bool,
) -> None:
    """Compare two compile envelopes A and B. Reports probe-family and
    classifier deltas. With --baseline, A is a prompt; the matching
    baseline replaces B."""
    json_mode = ctx.obj.get("json") if ctx.obj else False
    cwd = os.path.abspath(root)

    # ---- --update-baseline path: recompile A, overwrite, done.
    if update_baseline_dir is not None:
        try:
            current = _compile_envelope(a, cwd)
        except Exception as exc:  # noqa: BLE001 — surface, do not swallow
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "envelope-diff",
                            summary={
                                "verdict": "compile_failed",
                                "partial_success": True,
                                "error": str(exc)[:240],
                            },
                        )
                    )
                )
            else:
                click.echo(f"VERDICT: compile_failed\n  {exc}", err=True)
            ctx.exit(2)
            return
        task_root, _meta = _write_baseline(update_baseline_dir, a, current, cwd)
        verdict = f"baseline updated at {task_root}"
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "envelope-diff",
                        summary={
                            "verdict": verdict,
                            "partial_success": False,
                            "mode": "update_baseline",
                        },
                        baseline_meta=None,
                        baseline_path=str(task_root),
                    )
                )
            )
        else:
            click.echo(f"VERDICT: {verdict}")
        return

    # ---- --baseline path: compile A, load baseline as B.
    if baseline_dir is not None:
        baseline_env, baseline_meta = _load_baseline(baseline_dir, a)
        if baseline_env is None:
            _root, env_path, _meta_path = _baseline_paths(baseline_dir, a)
            msg = f"no baseline at {env_path}"
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "envelope-diff",
                            summary={
                                "verdict": "baseline_missing",
                                "partial_success": True,
                                "missing_path": str(env_path),
                            },
                            baseline_path=str(env_path),
                        )
                    )
                )
            else:
                click.echo(f"VERDICT: baseline_missing\n  {msg}", err=True)
            ctx.exit(2)
            return
        try:
            current = _compile_envelope(a, cwd)
        except Exception as exc:  # noqa: BLE001 — surface, do not swallow
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "envelope-diff",
                            summary={
                                "verdict": "compile_failed",
                                "partial_success": True,
                                "error": str(exc)[:240],
                            },
                        )
                    )
                )
            else:
                click.echo(f"VERDICT: compile_failed\n  {exc}", err=True)
            ctx.exit(2)
            return

        diff = _diff_envelopes(baseline_env, current)
        regression_findings = _compute_regression_findings(baseline_env, current)
        base_rate = _probe_fire_rate(baseline_env)
        cur_rate = _probe_fire_rate(current)
        _proc_b, conf_b = _classifier_signal(baseline_env)
        _proc_c, conf_c = _classifier_signal(current)

        # Verdict — LAW 6: must work without any other field.
        if regression and regression_findings:
            # Count missing families across findings for the verdict.
            missing_n = sum(
                len(f.get("missing_families", [])) for f in regression_findings if f["rule"] == "probe_family_missing"
            )
            if missing_n:
                verdict = f"REGRESSION: {missing_n} probe families dropped"
            else:
                verdict = f"REGRESSION: {len(regression_findings)} regression rules tripped"
        else:
            verdict = "PASS: no regression"

        _root_path, env_path, _meta_path = _baseline_paths(baseline_dir, a)
        facts = _build_regression_facts(
            regression_findings,
            baseline_meta,
            base_rate,
            cur_rate,
            conf_b,
            conf_c,
        )

        if json_mode:
            envelope = json_envelope(
                "envelope-diff",
                summary={
                    "verdict": verdict,
                    "partial_success": bool(regression_findings),
                    "mode": "baseline",
                    "regression_check": regression,
                    "regression_finding_count": len(regression_findings),
                    "probe_fire_rate_baseline": round(base_rate, 4),
                    "probe_fire_rate_current": round(cur_rate, 4),
                    "size_delta_bytes": diff["size_delta_bytes"],
                },
                agent_contract={
                    "facts": facts,
                    "next_commands": [
                        f'roam envelope-diff "{a[:40]}" --update-baseline {baseline_dir}',
                        "roam compile-cache stats",
                    ],
                    "risks": [],
                    "confidence": None,
                },
                baseline_meta=baseline_meta,
                baseline_path=str(env_path),
                regression_findings=regression_findings,
                added_probes=diff["added_probes"],
                removed_probes=diff["removed_probes"],
                changed_probes=diff["changed_probes"],
                size_delta_bytes=diff["size_delta_bytes"],
                classifier_delta=diff["classifier_delta"],
            )
            click.echo(to_json(envelope))
        else:
            click.echo(f"VERDICT: {verdict}")
            click.echo(f"baseline:            {env_path}")
            click.echo(f"fire_rate base→cur:  {base_rate:.3f} → {cur_rate:.3f}")
            click.echo(f"confidence base→cur: {conf_b:.3f} → {conf_c:.3f}")
            for f in regression_findings:
                # Rules 4/5 (reroute, artifact_downgrade) carry no
                # actual/threshold pair — print the keys they do have.
                detail = ", ".join(
                    f"{k}={f[k]}"
                    for k in (
                        "actual",
                        "threshold",
                        "baseline_procedure",
                        "current_procedure",
                        "baseline_artifact",
                        "current_artifact",
                        "baseline_bytes",
                        "current_bytes",
                    )
                    if k in f
                )
                click.echo(f"  ! {f['rule']} ({detail})")
        if regression and regression_findings:
            ctx.exit(5)
        return

    # ---- Default A-vs-B path (unchanged behavior).
    if b is None:
        msg = "envelope-diff requires two prompts unless --baseline or --update-baseline is used"
        if json_mode:
            click.echo(
                to_json(
                    json_envelope(
                        "envelope-diff",
                        summary={"verdict": "missing_argument", "partial_success": True, "error": msg},
                    )
                )
            )
        else:
            click.echo(f"VERDICT: missing_argument\n  {msg}", err=True)
        ctx.exit(2)
        return

    env_a: dict | None
    env_b: dict | None
    missing: list[str] = []

    if from_cache:
        env_a = _load_envelope_from_cache(cwd, a)
        env_b = _load_envelope_from_cache(cwd, b)
        if env_a is None:
            missing.append(a)
        if env_b is None:
            missing.append(b)
        if missing:
            msg = f"cache lookup found no envelope for {'/'.join(missing)} in {_cache_path(cwd)}"
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "envelope-diff",
                            summary={
                                "verdict": "cache_miss",
                                "partial_success": True,
                                "missing_keys": missing,
                            },
                        )
                    )
                )
            else:
                click.echo(f"VERDICT: cache_miss\n  {msg}", err=True)
            ctx.exit(2)
            return
    else:
        try:
            env_a = _compile_envelope(a, cwd)
            env_b = _compile_envelope(b, cwd)
        except Exception as exc:  # noqa: BLE001 — surface, do not swallow
            if json_mode:
                click.echo(
                    to_json(
                        json_envelope(
                            "envelope-diff",
                            summary={
                                "verdict": "compile_failed",
                                "partial_success": True,
                                "error": str(exc)[:240],
                            },
                        )
                    )
                )
            else:
                click.echo(f"VERDICT: compile_failed\n  {exc}", err=True)
            ctx.exit(2)
            return

    assert env_a is not None and env_b is not None
    diff = _diff_envelopes(env_a, env_b)
    added = diff["added_probes"]
    removed = diff["removed_probes"]
    changed = diff["changed_probes"]
    verdict = f"{len(added)} probe families added, {len(removed)} removed, {len(changed)} changed"

    facts = _build_facts(diff)

    if json_mode:
        envelope = json_envelope(
            "envelope-diff",
            summary={
                "verdict": verdict,
                "partial_success": False,
                "from_cache": from_cache,
                "size_delta_bytes": diff["size_delta_bytes"],
                "added_probe_count": len(added),
                "removed_probe_count": len(removed),
                "changed_probe_count": len(changed),
            },
            agent_contract={
                "facts": facts,
                "next_commands": [
                    "roam compile <task>",
                    "roam compile-cache stats",
                ],
                "risks": [],
                "confidence": None,
            },
            added_probes=added,
            removed_probes=removed,
            changed_probes=changed,
            size_delta_bytes=diff["size_delta_bytes"],
            classifier_delta=diff["classifier_delta"],
        )
        click.echo(to_json(envelope))
        return

    # Text mode
    cdelta = diff["classifier_delta"]
    click.echo(f"VERDICT: {verdict}")
    click.echo(f"size_delta:          {diff['size_delta_bytes']:+d} bytes")
    click.echo(f"procedure A → B:     {cdelta['procedure_a']} → {cdelta['procedure_b']}")
    click.echo(f"confidence A → B:    {cdelta['confidence_a']:.3f} → {cdelta['confidence_b']:.3f}")
    click.echo("")
    click.echo(f"added probes ({len(added)}):")
    for name in added:
        click.echo(f"  + {name}")
    click.echo(f"removed probes ({len(removed)}):")
    for name in removed:
        click.echo(f"  - {name}")
    click.echo(f"changed probes ({len(changed)}):")
    for entry in changed:
        click.echo(
            f"  ~ {entry['name']}  "
            f"({entry['size_delta_bytes']:+d} bytes, "
            f"+{len(entry['fields_added'])}/-{len(entry['fields_removed'])} fields)"
        )
