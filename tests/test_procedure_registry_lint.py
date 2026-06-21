"""Closed-enumeration lint over the compiler's per-procedure tables.

A new compile procedure needs ~15 integration sites (thresholds, contracts,
recommended-first, artifact policy, promotion keys, ...). Two historical
failure classes this lint pins:

* a table key that is not a real procedure (typo / rename leftover) sits
  inert forever — the `structural_query` entries below predate the
  structural_* family split and are pinned as documented legacy;
* a procedure missing from a REQUIRED table fails at dispatch time
  (the recommended-first KeyError class) instead of in CI.

Adding a procedure? Add it to ``CANONICAL_PROCEDURES`` here — the lint
failing on the new name is the checklist firing, not noise.
"""

from __future__ import annotations

from roam.plan import compiler as C

CANONICAL_PROCEDURES = frozenset(
    {
        "cli_verb_why_slow",
        "compare_x_vs_y",
        "config_where",
        "describe_file",
        "entry_point_where",
        "file_history",
        "freeform_explore",
        "refactor_move",
        "repo_structure",
        "self_contained_task",
        "session_meta",
        "stack_trace_fix",
        "structural_blast",
        "structural_callers",
        "structural_complexity",
        "structural_coupling",
        "structural_cycle",
        "structural_dead",
        "symbol_defined_where",
        "synthesis_query",
        "top_n_ranking",
        "trace_query",
    }
)

# Dead vocabulary kept on purpose (pre-split fallback rows the cache layer
# may still reference). New names must NOT land here without a reason.
LEGACY_PROCEDURES = frozenset({"structural_query"})

_TABLES = {
    "_PER_PROCEDURE_CONF_THRESHOLD": lambda: set(C._PER_PROCEDURE_CONF_THRESHOLD),
    "_RECOMMENDED_FIRST_COMMAND": lambda: set(C._RECOMMENDED_FIRST_COMMAND),
    "_PROCEDURE_CONTRACTS": lambda: set(C._PROCEDURE_CONTRACTS),
    "_PROCEDURE_PARALLEL_COMBO": lambda: set(C._PROCEDURE_PARALLEL_COMBO),
    "_PROBE_DISPATCH": lambda: set(C._PROBE_DISPATCH),
    "_PROCEDURE_PROBE_SKIPS": lambda: set(C._PROCEDURE_PROBE_SKIPS),
    "_ARTIFACT_POLICY": lambda: set(C._ARTIFACT_POLICY),
    "_PROCEDURE_BASE_CONFIDENCE": lambda: set(C._PROCEDURE_BASE_CONFIDENCE),
    "_L1_PROBE_ELIGIBLE": lambda: set(C._L1_PROBE_ELIGIBLE),
    "_L1_TASK_TEXT_TARGET_PROCEDURES": lambda: set(C._L1_TASK_TEXT_TARGET_PROCEDURES),
    "_L1_PROCEDURE_KEYS": lambda: set(C._L1_PROCEDURE_KEYS),
}


def test_every_table_key_is_a_known_procedure():
    allowed = CANONICAL_PROCEDURES | LEGACY_PROCEDURES | {"default"}
    for name, getter in _TABLES.items():
        unknown = getter() - allowed
        assert not unknown, (
            f"{name} carries keys that are not known procedures: {sorted(unknown)} — "
            f"typo, rename leftover, or a new procedure missing from CANONICAL_PROCEDURES."
        )


def test_required_tables_cover_every_procedure():
    """Tables consulted UNCONDITIONALLY at dispatch time must cover every
    canonical procedure — a miss is a runtime KeyError or a silent
    mis-route, not a degraded answer."""
    for required in ("_PER_PROCEDURE_CONF_THRESHOLD", "_RECOMMENDED_FIRST_COMMAND", "_ARTIFACT_POLICY"):
        keys = _TABLES[required]()
        missing = CANONICAL_PROCEDURES - keys
        assert not missing, f"{required} is missing procedures: {sorted(missing)}"


def test_classifier_confidence_has_explicit_bucket_per_procedure():
    """Every non-structural canonical procedure must carry an EXPLICIT
    confidence bucket in `_PROCEDURE_BASE_CONFIDENCE` — never a silent
    fall-through to `_DEFAULT_PROCEDURE_CONFIDENCE`.

    This pins the W-CONF asymmetry: `refactor_move` once had explicit
    `_PER_PROCEDURE_CONF_THRESHOLD` + `_ARTIFACT_POLICY` rows yet no
    confidence bucket, so it scored the 0.50 default while every sibling
    procedure scored from an intentional bucket. structural_* is exempt —
    its confidence is computed dynamically from subtype hit-count.
    """
    non_structural = {p for p in CANONICAL_PROCEDURES if not p.startswith("structural_")}
    buckets = set(C._PROCEDURE_BASE_CONFIDENCE)
    missing = non_structural - buckets
    assert not missing, (
        "non-structural procedures lack an explicit confidence bucket "
        f"(silent 0.50 default): {sorted(missing)} — add them to "
        "_PROCEDURE_BASE_CONFIDENCE."
    )


def test_classifier_confidence_buckets_are_valid_probabilities():
    """Every bucket score must be a probability in [0, 1]."""
    for proc, score in C._PROCEDURE_BASE_CONFIDENCE.items():
        assert 0.0 <= score <= 1.0, f"{proc} bucket {score!r} not in [0, 1]"
    assert 0.0 <= C._DEFAULT_PROCEDURE_CONFIDENCE <= 1.0


def test_refactor_move_confidence_preserved_at_default():
    """W-CONF preserved current scores: extraction must NOT change
    refactor_move's behavior. It was 0.50 (the else default) before the
    bucket existed; pin that until a deliberate retune wave moves it."""
    assert C._PROCEDURE_BASE_CONFIDENCE["refactor_move"] == C._DEFAULT_PROCEDURE_CONFIDENCE
    assert C._classifier_confidence("move open_db from a.py to b.py", "refactor_move") == 0.50


def test_l1_eligible_procedures_have_promotion_keys():
    """An L1-eligible procedure without promotion keys builds the probe
    envelope and then ALWAYS demotes — wasted probes on every call."""
    eligible = set(C._L1_PROBE_ELIGIBLE)
    keys = set(C._L1_PROCEDURE_KEYS)
    missing = eligible - keys
    assert not missing, f"L1-eligible but no _L1_PROCEDURE_KEYS entry: {sorted(missing)}"


def test_classifier_returns_only_canonical_procedures():
    """Sentinel prompts spanning every routing family must classify into
    the canonical set — a new procedure surfacing here without a
    CANONICAL_PROCEDURES entry is the checklist firing."""
    prompts = [
        "who calls open_db?",
        "which files depend on cli.py",
        "what breaks if I refactor open_db",
        "is open_db dead code",
        "how complex is open_db",
        "are there import cycles",
        "trace the login flow",
        "fix this: Traceback (most recent call last): File 'x.py', line 4, in f",
        "write a pytest for open_db",
        "where is open_db defined",
        "top 5 most imported files",
        "why is roam index slow",
        "compare open_db vs close_db",
        "what changed in cli.py last week",
        "what are the layers of this codebase",
        "where is the entry point",
        "where is the ROAM_GREP_ENGINE env var configured",
        "what does cli.py do",
        "lets keep going",
        "move open_db from connection.py to db.py",
        "explain how indexing works in general terms please",
    ]
    for p in prompts:
        proc, _ = C._classify(p)
        assert proc in CANONICAL_PROCEDURES, f"{p!r} -> {proc!r} not canonical"
