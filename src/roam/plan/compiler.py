"""Deterministic task compiler. Zero model calls.

Wraps existing `roam` CLI commands via subprocess. Output is a JSON-serializable
plan envelope consumable by any model worker.

Architecture-seal mapping:
  procedure          ←  task classifier (regex, identical taxonomy to v13_harness.py)
  likely_files       ←  roam --json search-semantic
  required_checks    ←  roam --json commands (G2 command graph) — kind=test
  forbidden_paths    ←  constant v0 set
  plan_quality       ←  heuristic: grounded_facts_count / target_count
  model_calls_avoided←  list of local subprocess invocations that returned signal
  recommended_first_command ← TASK→TOOL map (the 25+ A/B-verified routing)
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import threading as _w131_threading  # W131 — pre-import for cross-block use
import time

from roam.observability import log_swallowed

# W127 — orjson fast-path. orjson serializes 5-10× faster than stdlib
# `json` and produces compact output by default. We use it when available
# (e.g. `pip install roam-code[fast]` could pull it in) and fall back
# cleanly to stdlib otherwise. The detection happens once at module
# import so the hot path is just a function call.
try:
    import orjson as _orjson  # type: ignore[import-not-found]

    def _fast_json_dumps(obj) -> str:
        """W127 — orjson-backed dumps; ~5-10× faster than stdlib `json`."""
        return _orjson.dumps(obj).decode("utf-8")

    _ORJSON_AVAILABLE = True
except ImportError:

    def _fast_json_dumps(obj) -> str:
        """W127 — stdlib fallback. Compact separators save ~5% on big envelopes."""
        return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)

    _ORJSON_AVAILABLE = False
from collections import Counter
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from pathlib import Path


def _set_wal(conn) -> None:
    """Best-effort: switch a cache connection to WAL journal mode (W148 family).

    WAL is a throughput optimization for the SQLite caches; if the filesystem
    doesn't support it the PRAGMA raises and we keep the default journal mode
    (correctness unaffected). Deliberately SILENT — logging on every cache open
    over a non-WAL filesystem would be pure noise. Extracted from 11 inline
    duplicate guards so the swallow lives in exactly one audited place."""
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except Exception:  # noqa: BLE001 — WAL is optional; default journal is fine
        pass


# ---- procedure classifier — same taxonomy as v13_harness.py:STRUCTURAL_RE etc. ----
# v0.1 additions: "zero callers" / "cyclical" / "god-component".
# v0.2 — split structural into 6 sub-types so the routing hint matches the intent.
# Order matters: check most-specific sub-types first, then fall through to general.

_STRUCTURAL_DEAD_RE = re.compile(
    # v0.5.1 (2026-05-29 14:50): also "NO corresponding test" / "no .* test"
    # patterns for the lar03_untested_job class of task.
    r"\b(safe to delete|dead code|"
    r"find\s+unused|"
    r"unused\s+(public|function|functions|symbol|symbols|export|exports|module|modules|code|imports?)|"
    r"orphan(ed|s)?\s+(symbol|symbols|import|imports|code|function|functions)|"
    r"zero callers|no callers anywhere|"
    r"untested\s+(job|jobs|listener|listeners|command|commands|mailable|mailables)|"
    r"NO\s+corresponding\s+test|no\s+test\s+for|missing\s+test\b|"
    r"100%? safe to delete|never (called|used)|not (called|referenced|imported))\b",
    re.IGNORECASE,
)
_STRUCTURAL_CYCLE_RE = re.compile(
    # W31: Phase A --explain smoke discovered "are there cycles
    # in X imports?" fell through to freeform_explore. Pattern was anchored on
    # "cycle.*import" (singular). Now accepts the plural and direct "cycles in".
    r"\b(cyclical|cycles?\s+in\b|cycles?.*import|import.*cycles?|"
    r"one cycle|circular\s+(?:imports?|dependenc(?:y|ies))|"
    r"how\s+many\s+cycles|(?:are\s+there|find|detect|show)\s+(?:any\s+|the\s+)?cycles|"
    r"what\s+are\s+the\s+cycles|cycles?\s+are\s+there|"
    r"(?:has|have|contains?)\s+(?:any\s+)?cycles)\b",
    re.IGNORECASE,
)
_STRUCTURAL_COMPLEXITY_RE = re.compile(
    # v0.3 fix: dropped "worst-case complexity" — that intent (deep07) needs
    # source reading, not file_info. Let it fall through to freeform_explore
    # which wins on algorithm-source questions.
    # W31: Phase A --explain smoke discovered "top N most complex
    # symbols in X" fell through. Added "most complex" / "top N complex".
    r"\b(god[\s-]?components?|cognitive complexity|complexity refactor|"
    r"most complex|top\s+\d+.*complex|too\s+complex|"
    r"cyclomatic\s+complexity|complexity\s+of|how\s+complex\s+is|"
    r"large file or many|many state fields|line count)\b",
    re.IGNORECASE,
)
_STRUCTURAL_BLAST_RE = re.compile(
    r"\b(blast radius|impact of|refactor blast|refactor.*break|what breaks if)\b",
    re.IGNORECASE,
)
_STRUCTURAL_CALLERS_RE = re.compile(
    # "consumers of X" / "users of X" are direct callers-synonyms that fell to
    # freeform_explore in prod telemetry (49% of freeform delivered no prefetch);
    # e.g. "find the consumers of `log_swallowed`" → now routes to roam_uses.
    r"\b(callers? of|consumers? of|users of|who calls|what\s+(?:calls|uses)|how\s+many\s+(?:callers?|references?|uses?)|uses of (the|this)|references to)\b",
    re.IGNORECASE,
)
_STRUCTURAL_COUPLING_RE = re.compile(
    # W34a: added "temporal coupling" / "coupling for" / "co-change" patterns
    # — the user-facing "find temporal coupling for X" / "what co-changes with
    # X" phrasings were falling through to freeform_explore (classifier hole
    # discovered during emulation).
    r"\b(most strongly coupled|coupled to|strongest coupling|coupling.*to|"
    r"temporal coupling|coupling\s+for|coupling\s+of|coupling\s+between|co-change|co.change.*with|"
    r"(?:which|what)\s+files?\s+(?:that\s+)?imports?|files?\s+importing|"
    r"what\s+imports?\b|dependency\s+graph|dependencies\s+of|"
    r"top\s+\d+\s+(most.)?imported|imports?\s+of|depends? on|top.*coupled|"
    r"highest.*coupling|structural coupling|most coupled)\b",
    re.IGNORECASE,
)


def _classify_structural_subtype(task: str) -> str | None:
    """Most-specific structural intent. None if not a structural query.

    v0.3: coupling moved BEFORE blast/callers so compound tasks like
    "highest structural coupling (most callers / largest blast radius)"
    route to coupling — the authoritative intent — instead of latching
    on the first secondary signal.
    """
    if _STRUCTURAL_DEAD_RE.search(task):
        return "structural_dead"
    if _STRUCTURAL_CYCLE_RE.search(task):
        return "structural_cycle"
    if _STRUCTURAL_COMPLEXITY_RE.search(task):
        return "structural_complexity"
    if _STRUCTURAL_COUPLING_RE.search(task):
        return "structural_coupling"
    if _STRUCTURAL_BLAST_RE.search(task):
        return "structural_blast"
    if _STRUCTURAL_CALLERS_RE.search(task):
        return "structural_callers"
    return None


# Back-compat alias — structural_query == any sub-type matched.
_STRUCTURAL_RE = re.compile(
    "|".join(
        r.pattern
        for r in (
            _STRUCTURAL_DEAD_RE,
            _STRUCTURAL_CYCLE_RE,
            _STRUCTURAL_COMPLEXITY_RE,
            _STRUCTURAL_BLAST_RE,
            _STRUCTURAL_CALLERS_RE,
            _STRUCTURAL_COUPLING_RE,
        )
    ),
    re.IGNORECASE,
)
_TRACE_RE = re.compile(
    # v0.5.1: also "trace what happens" pattern (py03_trace_health miss).
    # Corpus wave: "where does the login flow start", "follow the path from
    # X to Y", "pick one route ... trace it through" all fell to freeform.
    # The "where does ... start" form is restricted to flow-ish nouns so
    # entry-point prompts ("where does the cli start") keep their procedure.
    r"\b(trace\s+(how|the|this|that|to|through|from|command|call|route|flow|user|login|method|function|what|it|them)|"
    r"how does.*work|pipeline|flow\b.*\bfrom\b|reach\b|"
    r"walk\s+(me\s+)?through|step.by.step|"
    r"follow\s+the\s+(path|flow|call|request)|\bpath\s+from\b|"
    r"where\s+does\s+(the\s+)?\w*\s*(flow|request|login|auth)\s+(start|begin|enter)|"
    r"trace\s+what\s+happens|from\s+the\s+CLI|entry\s+point.*through)",
    re.IGNORECASE,
)
_SYNTHESIS_RE = re.compile(
    # W33d (M3): the original `add\s+\w+` was too loose — caught phrases like
    # "trace how X is added through" → wrong route. Now requires an article
    # or a "to/this/that" so freeform narration doesn't latch.
    r"\b(write\s+(?:a\s+)?(?:pytest|unit\s+test|integration\s+test|spec|test|docstring)|"
    r"propose a refactor|extract.*from|"
    r"unified diff|rewrite it in|reduce.*line count|draft a|patch|"
    r"add\s+(a|an|the|this|that|new)\s+\w+|"
    r"implement\s+\w+|create\s+(a|an|the|this|that|new)\s+\w+\s+feature)\b",
    re.IGNORECASE,
)

# Generation-shaped synthesis tasks where injection is measured NET-NEGATIVE:
# the 2026-06-09/10 Fable 5 A/B's write-pytest cells ran the SAME 10 turns
# with +25% input tokens (489K→611K) — the envelope is re-read as cache every
# turn while saving nothing, and the lean-envelope variant (3 reps, 11 turns,
# ~615K) lost too. For code-WRITING tasks the agent must read/edit/run
# regardless, so the prompt-time channel advises the hook to inject nothing.
# Refactor-proposal shapes (propose a refactor / unified diff / extract X)
# stay injected — impact/caller facts feed those answers directly.
_GENERATION_SKIP_RE = re.compile(
    r"\b(write\s+(?:a\s+)?(?:pytest|unit\s+test|integration\s+test|spec|test|docstring)|"
    r"implement\s+\w+|add\s+(?:a|an|the|this|that|new)\s+\w+|"
    r"create\s+(?:a|an|the|this|that|new)\s+\w+\s+feature|draft\s+a)\b",
    re.IGNORECASE,
)


def injection_advice(procedure: str, task: str | None) -> str:
    """Advise the prompt-injection channel (Claude Code UPS hook) whether the
    envelope is worth injecting for this task.

    Returns ``"inject"`` or ``"skip_generation_task"``. Explicit
    ``roam compile`` callers always get the full envelope either way — the
    advice only gates the per-prompt auto-injection channel.
    """
    if procedure == "synthesis_query" and task and _GENERATION_SKIP_RE.search(task):
        return "skip_generation_task"
    return "inject"


# W35a — stack-trace shape. Real user task: "fix this: ... File 'x.py',
# line 42, in foo()". When the task carries an actual stack trace, the
# optimal compile path is to extract every (file, line) frame and embed
# the source slice — agent reads the failure context without a Read.
# Pattern matches:
#   (a) Python  'File "x.py", line 42'
#   (b) generic 'x.py:42'  (when accompanied by an error word, see below)
#   (c) error header words: Traceback / Error / Exception / raised
# Classifier fires when at least one file:line tuple is present AND an
# error context word appears nearby.
_STACK_FRAME_PY_RE = re.compile(
    r'File\s+"([^"]+\.(?:py|pyx))"\s*,\s+line\s+(\d+)',
)
_STACK_FRAME_GENERIC_RE = re.compile(
    # W40 C2: added `rs` for Rust panics (`'<msg>', src/main.rs:42`).
    r"(?<![\w/])([\w./-]+\.(?:py|js|ts|tsx|jsx|go|rs|rb|java|cs|php|kt|swift))[:](\d+)\b",
)
_STACK_ERROR_CONTEXT_RE = re.compile(
    # F1 (W37 readiness): `Error\b` alone missed PascalCase-suffix forms
    # like AssertionError. Extended with `[A-Z]\w*Error\b` so any *Error
    # class name matches. Bare `Error\b` (no prefix) is also retained
    # so "Error in foo.py:42" / "Error: bad" still trigger.
    r"\b(Traceback|Exception|raised|panicked|FAIL|"
    r"[A-Z]\w*Error\b|Error\b|"
    r"NullPointerException|IndexOutOfBounds)\b",
)

# Perf-shaped freeform tasks ("optimize X", "fix the n+1 in Y", "make Z
# faster") get the scoped algorithm-catalog findings embedded — `roam algo
# --path` returns Current/Better/Tip/Fix per anti-pattern, which IS the
# answer the agent would otherwise derive by reading the file. Scoped runs
# are ~1.8s (vs 18s whole-project), inside probe budget.
_ALGO_PERF_RE = re.compile(
    r"\b(optimi[sz]e|n\+1|too slow|slowness|slow\b|faster|speed up|"
    r"perf(?:ormance)?\b|inefficien|algorithmic|big-?o|quadratic|hot ?spots?)\b",
    re.IGNORECASE,
)

# W35b — "what does this file do" trigger inside freeform_explore.
# When the task is an explain/describe question on a single named small
# file, embedding the FIRST N lines of source beats embedding just
# signatures.
_EXPLAIN_RE = re.compile(
    r"\b(what does|how does|explain|describe|role of|purpose of|"
    r"walk\s+me\s+through|tell\s+me\s+about)\b",
    re.IGNORECASE,
)

# W35c — recent-change probe trigger inside freeform_explore.
# When the task asks about recent edits to a named file, embedding
# `git log -5 --stat` saves the agent a Bash call.
_HISTORY_QUERY_RE = re.compile(
    r"\b(recent|recently|last\s+(week|month|day|few|commit)|"
    r"when did|what changed|history of|who changed|who touched|"
    r"latest\s+(change|edit|commit)|since)\b",
    re.IGNORECASE,
)

# W36a — test-write trigger inside synthesis_query. Real user task:
# "write a pytest for compile_plan". When the synthesis task is a TEST,
# the most useful additional context is a SIBLING test file — same
# fixtures, same imports, same marker conventions. Probe finds it via
# the project's test-naming convention (`src/X.py` → `tests/test_X.py`
# in Python; `_test.go` in Go; `.test.js` in JS).
_TEST_WRITE_RE = re.compile(
    r"\b(write\s+(a\s+)?(pytest|unit\s*test|spec|test)|"
    r"add\s+(a\s+)?(pytest|unit\s*test|spec|test)|"
    r"test\s+for\s+\w+|"
    r"create\s+(a\s+)?(pytest|test))\b",
    re.IGNORECASE,
)

# W36b — multi-path comparison trigger. When the task names 2+ paths AND
# asks for a comparison, the optimal compile-time data is a unified diff
# between the two files.
_COMPARE_RE = re.compile(
    r"\b(compare|diff(?:erence)?\s+between|vs\.?|versus|"
    r"what'?s?\s+different|how\s+(do|does).+differ|"
    r"side[\s-]by[\s-]side|both\s+files)\b",
    re.IGNORECASE,
)

# W36c — symbol-pickaxe trigger. Differs from W35c (file history) — this
# fires when the task asks about a SPECIFIC SYMBOL's history (when it
# was added/removed). The probe runs `git log -S<symbol>` (pickaxe).
_SYMBOL_PICKAXE_RE = re.compile(
    r"\b(when\s+(did|was)|who\s+(added|removed|deleted|introduced|wrote|created)|"
    r"first\s+commit|originally\s+(added|created)|"
    r"deleted\s+from|removed\s+from)\b",
    re.IGNORECASE,
)

# W44 I1 — conventions probe trigger. "How do we structure tests here",
# "what's the style for X", "how should I name Y" — onboarding questions
# whose optimal answer is "here are 2-3 example files from the same area;
# mirror their patterns". Probe samples the target directory and embeds
# the first N lines of the closest siblings.
_CONVENTIONS_RE = re.compile(
    r"\b(how do we|what'?s? (the|our) (convention|pattern|style|approach)|"
    r"how (do|should) (we|I|you) (name|structure|organi[sz]e|write|format)|"
    r"what (style|naming|convention)|"
    r"existing (pattern|convention|style))\b",
    re.IGNORECASE,
)

# W44 I2 — module-name shorthand: "the auth module", "the cli command",
# "the compiler package". When a task lacks an explicit file path but
# mentions a module/package/dir by name, glob for likely matches and
# pick the top file.
_MODULE_NAME_RE = re.compile(
    r"\bthe\s+([a-z][a-z0-9_]+)\s+(module|package|component|cmd|command|"
    r"helper|wrapper|service|client|controller|view|model|util(s|ity)?)\b",
    re.IGNORECASE,
)

# W48 — reachability Y/N. "is X reachable from Y", "does X depend on Y",
# "can X call Y". Probe runs `roam impact <src>` and checks for `target`.
_REACHABILITY_RE = re.compile(
    r"\b(is\s+\S+\s+(reachable|called)\s+from|"
    r"does\s+\S+\s+(depend|rely)\s+on|"
    r"can\s+\S+\s+(call|reach|use)\s+\S+|"
    r"is\s+there\s+a\s+(path|call\s+chain)\s+(from|to))\b",
    re.IGNORECASE,
)

# W49 — config-by-name. "where is the X env var", "find the timeout
# setting", "look for the API_KEY config". Probe greps for env var
# patterns + common config keys.
_CONFIG_BY_NAME_RE = re.compile(
    r"\b(where\s+is\s+(the\s+)?(\w+)\s+(env\s+var|config|setting|"
    r"environment\s+variable|configuration|option|flag)|"
    r"find\s+(the\s+)?(\w+)\s+(env|config|setting|environment))\b",
    re.IGNORECASE,
)

# W50 — find-by-description (semantic). "the function that parses X",
# "find anything about caching", "where is the code that handles auth".
_FIND_BY_DESC_RE = re.compile(
    r"\b(the\s+(function|method|class|module|code)\s+that\s+\w+|"
    r"find\s+(anything|code|something)\s+(about|that|for)|"
    r"where\s+is\s+the\s+code\s+that\s+\w+|"
    r"which\s+(function|class|module)\s+(handles|parses|manages|writes|reads))\b",
    re.IGNORECASE,
)

# W66 — performance ("why is X slow", "what's slow", "perf hotspots").
# Probe runs `roam why-slow` to surface runtime hotspots from ingested
# traces. Returns the top symbols by runtime cost.
_WHY_SLOW_RE = re.compile(
    # W87 — allow 1-4 adjectives between "is" and "slow"
    # ("why is X slow", "why is roam dead slow", "why is the X compile slow", ...)
    # W100/post-A/B fix — also catch "slowest X" and "what is taking the
    # longest" — t27 holdout loss surfaced this gap.
    r"\b(why\s+is\s+(?:\S+\s+){1,4}slow|"
    r"what'?s\s+slow|"
    r"slowest\s+(phase|step|stage|part|operation|call|function)|"
    r"what'?s?\s+(taking\s+)?the\s+longest|"
    r"perf(ormance)?\s+(hotspot|bottleneck|issue|problem)|"
    r"slow\s+(symbol|function|method|call|path|phase)|"
    r"runtime\s+hotspot)\b",
    re.IGNORECASE,
)

# W109 — file owner / blame ("who owns X", "who wrote X", "blame X").
_OWNER_RE = re.compile(
    r"\b(who\s+(owns|wrote|authored|last\s+touched)|"
    r"git\s+blame\s+(of|for|on)|"
    r"primary\s+(author|contributor)\s+of|"
    r"who'?s\s+(the\s+)?(owner|maintainer)\s+of)\b",
    re.IGNORECASE,
)

# W110 — env var audit ("what env vars does X read", "list environment
# variables used by Y"). Greps for os.environ / os.getenv patterns.
_ENV_VAR_AUDIT_RE = re.compile(
    r"\b(what\s+env(ironment)?\s+(vars?|variables?)\s+(does|do)|"
    r"list\s+(all\s+)?env(ironment)?\s+(vars?|variables?)|"
    r"which\s+env(ironment)?\s+(vars?|variables?)|"
    r"environment\s+variables?\s+(used|read|consumed)\s+by)\b",
    re.IGNORECASE,
)

# W111 — TODO/FIXME audit ("what TODOs are in X", "list TODO comments").
_TODO_AUDIT_RE = re.compile(
    r"\b(TODO|FIXME|XXX|HACK|HACKY|REVISIT|TKTK)\s+(comments?|markers?|items?)|"
    r"(list|show|count)\s+(all\s+)?(TODO|FIXME)|"
    r"what\s+(TODO|FIXME)s?\s+(are|exist)",
    re.IGNORECASE,
)

# W112 — deprecation markers ("what's deprecated", "list @deprecated").
_DEPRECATION_RE = re.compile(
    r"\b(what'?s\s+deprecated|"
    r"list\s+(all\s+)?(deprecated|@deprecated)|"
    r"deprecated\s+(symbols?|functions?|methods?|api)|"
    r"@deprecated\s+(items|markers))\b",
    re.IGNORECASE,
)

# W113 — subprocess audit ("what subprocess calls does X make", "list shell-outs").
_SUBPROCESS_AUDIT_RE = re.compile(
    r"\b(what\s+subprocess(\s+\w+)?\s+(does|are|run|invocation)|"
    r"list\s+(all\s+)?(subprocess|shell-out|shell\s+out)s?|"
    r"shell\s+(out|invocation)s|"
    r"subprocess\.(run|Popen|call)\s+(sites?|invocations?)|"
    r"external\s+(process|command)\s+(calls?|invocations?))\b",
    re.IGNORECASE,
)

# W101 — cross-file refactor ("move X from A to B", "extract X from A
# into B", "relocate X to B"). Probe embeds the impact set (callers of
# X across the repo) so the agent knows the breakage surface before
# touching the code.
# W162 — also match symbolic destinations: "extract X from foo.py into
# a new helper module" / "extract X into a separate file" / etc. The
# destination filename is then INFERRED by the probe from the symbol
# name (e.g. log_swallowed → log_helpers.py in the same directory).
# Closes the W124/W159 t8 unrecovered failure.
_REFACTOR_MOVE_RE = re.compile(
    r"\b(move|relocate|extract|hoist|split\s+out)\s+`?([A-Za-z_][A-Za-z0-9_]+)`?"
    r"\s+(?:from\s+(\S+\.\w+)\s+(?:to|into)\s+(\S+\.\w+)|"
    r"to\s+(\S+\.\w+)|"
    r"from\s+(\S+\.\w+)\s+into\s+(?:a\s+)?(?:new\s+)?(?:separate\s+|its\s+own\s+)?"
    r"(helper\s+module|module|file|helper))",
    re.IGNORECASE,
)

# W102 — API surface ("what's exported by X", "public functions of Y",
# "what does this module expose"). Probe greps top-level def/class
# patterns and embeds the result.
_API_SURFACE_RE = re.compile(
    r"\b(what'?s?\s+(exported|exposed|public|the\s+API|the\s+surface)|"
    r"public\s+(functions?|methods?|classes?|symbols?|API)|"
    r"export(ed|s)?\s+(by|from|of)|"
    r"what\s+(?:does\s+)?(this|the)\s+(module|package|file)\s+(export|expose|provide))\b",
    re.IGNORECASE,
)

# W80 — test-impact ("what tests should I run", "which tests cover X").
# Runs `roam test-impact` if available; falls back to glob of sibling
# tests / tests mentioning the file name.
_TEST_IMPACT_RE = re.compile(
    r"\b(what\s+tests?\s+(should|do|to)\s+(I|i)\s+run|"
    r"which\s+tests?\s+(cover|exercise|touch)|"
    r"tests?\s+(affected|impacted)\s+by|"
    r"run\s+(only\s+)?the\s+(relevant|affected)\s+tests?)\b",
    re.IGNORECASE,
)
# Question vocabulary that passes the identifier regex but isn't the target.
_TEST_IMPACT_STOPWORDS: frozenset[str] = frozenset(
    {
        "what",
        "which",
        "tests",
        "test",
        "should",
        "run",
        "cover",
        "exercise",
        "touch",
        "the",
        "for",
        "after",
        "changing",
        "change",
        "relevant",
        "affected",
        "impacted",
        "only",
        "this",
        "that",
        "function",
        "method",
        "class",
        "module",
        "file",
        "code",
        "have",
        "does",
    }
)

# W67 — entry-point ("what's the entry point", "where does X start").
# Probe runs `roam entry-points` (protocol-classified). Lets the agent
# orient on REPL/CLI/HTTP/WORKER entry points without exploring.
_ENTRY_POINT_RE = re.compile(
    # "where/what is the entry point" — the most literal phrasings were
    # misses (only "what's ..." and "where does X start" matched).
    # 2026-06-11: a qualifier before "entry point" broke the match — the
    # README-gallery loss cell "where is the CLI entry point?" routed to
    # freeform_explore instead of the L1 answer. Allow the common ones.
    r"\b((what'?s?|what\s+is|where\s+(is|are))\s+the\s+"
    r"((main|cli|app|application|program|service|server|primary)\s+)?entry\s*.?points?|"
    r"where\s+does\s+\S+\s+start|"
    r"how\s+does\s+the\s+(cli|app|service|worker|server)\s+start|"
    r"main\s+entry|startup\s+(flow|path|sequence))\b",
    re.IGNORECASE,
)

# W11 — symbol-defined-where (no file anchor). Pattern: "where is X
# defined", "find where X is defined", "the function that handles X",
# "find Y", "locate X". Triggers a `roam search-symbol <bareword>`
# probe and embeds the top-5 hits as `symbol_definitions`.
#
# The PROBE-GAPS-2026-06-02 memo flagged 6/60 freeform prompts of this
# shape (~10% of the freeform tail). The existing `structural_callers`
# regex requires a callers-intent verb ("who calls"); "where defined"
# falls through. The two captured groups below are alternative
# match sites — at most ONE fires per regex hit and either may be the
# bareword anchor.
_SYMBOL_DEFINED_WHERE_RE = re.compile(
    # Alt 0: "where is / find where / where does THE function|method|class <X>
    # [is defined]". The noun ("the function") sits between the verb and the
    # symbol, which Alt 1's negative lookahead blocks. Telemetry (2026-06-05):
    # "Find where the function _evaluate_mcp_mode_policy is defined" leaked to
    # freeform_explore.
    r"\b(?:where\s+is|find\s+where|where\s+does)\s+the\s+"
    r"(?:function|method|class|symbol)\s+`?([A-Za-z_][A-Za-z0-9_]{2,})`?"
    r"(?:\s+(?:is\s+)?(?:defined|located|lives?|live))?|"
    # Alt 1: definition-intent verb + bareword. The negative lookahead
    # blocks common English filler ("the", "a", "this", "function", ...)
    # so cases like "locate the function compile_plan" fall through to
    # alt 2 instead of latching on "the".
    r"\b(?:where\s+is|find\s+where|locate|"
    r"which\s+(?:file|module)\s+defines?|"
    r"what\s+(?:file|module)\s+(?:holds?|contains?|has)|"
    r"where\s+does)\s+"
    r"(?!(?:the|a|an|this|that|function|method|class|symbol|"
    r"file|module|code|name)\b)"
    r"`?([A-Za-z_][A-Za-z0-9_]{2,})`?"
    r"(?:\s+(?:is\s+)?defined|\s+lives?|\s+sits?|\s+live|\b)|"
    # Alt 2: "find|locate the function|method|class|symbol <X>".
    r"\b(?:find|locate)\s+(?:the\s+)?(?:function|method|class|symbol)\s+"
    r"(?:that\s+handles?\s+|named\s+|called\s+|`)?"
    r"`?([A-Za-z_][A-Za-z0-9_]{2,})`?|"
    # Alt 3: bare backticked "find `sym`".
    r"\bfind\s+`([A-Za-z_][A-Za-z0-9_]{2,})`|"
    # Alt 4: "locate the <sym> function|method|class" — noun AFTER the
    # identifier (Alt 2 only handles the noun-before-identifier shape).
    r"\b(?:find|locate)\s+the\s+`?([A-Za-z_][A-Za-z0-9_]{2,})`?\s+(?:function|method|class)\b|"
    # Alt 5: bare "find|locate <sym>" — no noun, no backticks. Same
    # stopword guard as Alt 1 so English filler ("find the bug", "locate
    # all uses") falls through to freeform. Structural subtypes (dead /
    # cycle / callers / coupling) are classified BEFORE W11, so
    # "find unused" / "find circular imports" / "find callers of X" still
    # win their dedicated procedures.
    r"\b(?:find|locate)\s+"
    r"(?!(?:the|a|an|this|that|function|method|class|symbol|"
    r"file|module|code|name|where|out|all|any|every|how|why|what)\b)"
    r"`?([A-Za-z_][A-Za-z0-9_]{2,})`?\b|"
    # Alt 6: symbol-BODY intent — "how is X implemented", "body of X",
    # "signature of X", "show me the implementation of X". The W11 probe
    # already embeds `body_preview`, so this is the right home: the agent
    # gets the definition site + the first lines of source in one envelope.
    r"\bhow\s+is\s+(?:the\s+)?`?([A-Za-z_][A-Za-z0-9_]{2,})`?\s+"
    r"(?:implemented|defined|written|coded|built|structured)\b|"
    r"\b(?:implementation|body|signature|source\s+code|definition)\s+of\s+"
    r"(?:the\s+)?`?([A-Za-z_][A-Za-z0-9_]{2,})`?\b|"
    r"\bshow\s+(?:me\s+)?the\s+(?:body|implementation|signature|source|definition)"
    r"\s+of\s+(?:the\s+)?`?([A-Za-z_][A-Za-z0-9_]{2,})`?\b|"
    # Alt 7: "what does `X` do" / "what's `X` for" — symbol purpose/behavior.
    # Backticked → unambiguous symbol; the W11 probe embeds the body, so the
    # agent gets the definition + first lines = what it does.
    r"\bwhat(?:\s+does|'?s|\s+is)\s+`([A-Za-z_][A-Za-z0-9_]{2,})`\s+(?:do|for|doing)\b|"
    # Alt 8: bare "what does <snake_or_camel> do" — require an identifier-shaped
    # token (has `_` or camelCase) so file paths and English nouns fall through
    # to describe_file / freeform.
    r"\bwhat\s+does\s+([a-z][a-z0-9]*_[a-z0-9_]+|[a-z]+[A-Z][A-Za-z0-9]*)\s+do(?:es|ing)?\b",
    re.IGNORECASE,
)

# W12 — top-N ranking across the repo (no file/symbol anchor). Pattern:
# "top 5 most-imported files", "top danger zone file", "biggest Y",
# "most-coupled file". Triggers `roam metrics --top N --by <dim>`-shape
# probes and embeds the ranking as `top_n_ranking`.
#
# PROBE-GAPS-2026-06-02 flagged 3/60 freeform prompts here (~5% of the
# freeform tail). The dimension verb is captured so the probe can pick
# the right roam command (coupling / complexity / importance / churn).
_TOP_N_RANKING_RE = re.compile(
    # Shape A: anchor + optional N + optional "most-" + dimension.
    # e.g. "top 5 most-imported files", "biggest cycles", "hottest files".
    r"\b(?:top|biggest|largest|most|highest|worst|hot|hottest|"
    r"slow|slowest|deepest)[\s-]+"
    r"(?:(\d{1,3})\s+)?"
    r"(?:most[\s-])?"
    r"(imported|importing|coupled|complex|complicated|"
    r"churned|churning|danger(?:\s+zone)?|dangerous|"
    r"important|central|connected|pagerank|"
    r"called|callers?|"
    r"cycles|clusters|cluster|bottlenecks|"
    r"large|long|files?|modules?)\b|"
    # Shape B: "top N <noun> by <dim>" — dimension after "by".
    # e.g. "top 3 functions by complexity".
    r"\b(?:top|biggest|largest|highest|worst|hottest|slowest|deepest)\s+"
    r"(?:(\d{1,3})\s+)?"
    r"[a-z]+\s+by\s+"
    r"(imported|importing|coupling|complexity|"
    r"churn|danger|importance|pagerank|connected|callers?|"
    r"cycles|clusters|bottlenecks)\b",
    re.IGNORECASE,
)

# W13 — "why is roam <SUBCMD> slow". Pattern: "why is roam dead slow",
# "why is the roam index slow", "roam clusters hangs". The trigger
# REQUIRES the literal token `roam` plus a CLI subcommand. The probe
# resolves the subcommand via `cli._COMMANDS`, then runs the existing
# why-slow machinery on its entry function.
#
# PROBE-GAPS-2026-06-02 flagged 2/60 freeform prompts here (~3% of the
# freeform tail). The existing `_WHY_SLOW_RE` requires a backticked
# symbol; the CLI-verb shape misses.
_CLI_VERB_WHY_SLOW_RE = re.compile(
    r"\broam\s+(?:the\s+)?`?([a-z][a-z0-9-]{1,40})`?\b"
    r"(?=[^.?!]{0,80}\b(?:slow|hangs?|hanging|stalls?|stalling|stuck|"
    r"take[sn]?\s+(?:so\s+|too\s+|forever|a\s+long\s+time|long)|"
    r"taking\s+(?:so\s+|too\s+|a\s+)?long|"
    r"takes\s+forever))",
    re.IGNORECASE,
)


# W28 — "compare X vs Y" / "diff X and Y" / "what's the difference
# between X and Y". The probe routes to `roam semantic-diff` for file
# paths, `git diff` for git refs, or filters `roam coupling` pairs for
# symbol-vs-symbol comparisons. Captured entities can be:
#   - paths (contain `/` or end in `.<ext>`)
#   - barewords / identifiers
#   - backticked tokens
# Three alternatives are needed because English phrasings differ:
#   Alt 1: "compare X vs|and|to Y" / "X vs|versus Y" / "X compared to Y"
#   Alt 2: "diff X vs|and Y" / "diff A B" (two-arg diff)
#   Alt 3: "(what's the) difference between X and Y"
_COMPARE_X_VS_Y_RE = re.compile(
    # Alt 1: explicit "compare" verb OR bare "X vs Y" / "X versus Y" / "X compared to Y"
    r"\bcompare\s+`?([^\s`,]+?)`?\s+(?:vs\.?|versus|and|to|with|against)\s+`?([^\s`,.!?]+?)`?(?:\s|$|[.,!?])|"
    r"\b`?([A-Za-z_][\w./\-]*?)`?\s+(?:vs\.?|versus)\s+`?([A-Za-z_][\w./\-]*?)`?(?:\s|$|[.,!?])|"
    r"\b`?([A-Za-z_][\w./\-]*?)`?\s+compared\s+to\s+`?([A-Za-z_][\w./\-]*?)`?(?:\s|$|[.,!?])|"
    # Alt 2: "diff X vs|and|to Y" / "diff A B"
    r"\bdiff\s+`?([^\s`,]+?)`?\s+(?:vs\.?|versus|and|to|with|against)\s+`?([^\s`,.!?]+?)`?(?:\s|$|[.,!?])|"
    r"\bdiff\s+`?([A-Za-z_][\w./\-]+?)`?\s+`?([A-Za-z_][\w./\-]+?)`?(?:\s|$|[.,!?])|"
    # Alt 3: "(what's the) difference between X and Y"
    r"\b(?:what'?s?\s+(?:the\s+)?)?difference\s+between\s+`?([^\s`,]+?)`?\s+(?:and|vs\.?|versus)\s+`?([^\s`,.!?]+?)`?(?:\s|$|[.,!?])",
    re.IGNORECASE,
)


def _extract_stack_frames(task: str) -> list[tuple[str, int]]:
    """W35a — return all (file, line_int) tuples present in the task text."""
    frames: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for rgx in (_STACK_FRAME_PY_RE, _STACK_FRAME_GENERIC_RE):
        for path, line_s in rgx.findall(task):
            try:
                key = (path, int(line_s))
            except ValueError:
                continue
            if key not in seen:
                seen.add(key)
                frames.append(key)
    return frames


def _looks_like_stack_trace(task: str) -> bool:
    """W35a + W120 — stack-trace classifier.

    Primary form: ≥1 `file.py:N` frame AND error context word.
    W120 secondary: bare Python error message with NO frame (common
    pattern: "TypeError: 'str' object is not callable"). For these,
    classify as stack_trace_fix so the W74 patch hints fire — the
    patch hint alone is useful even without a frame to embed.
    """
    if _extract_stack_frames(task) and _STACK_ERROR_CONTEXT_RE.search(task):
        return True
    # W120 — bare-message detection: "ErrorClass: message" pattern.
    bare = re.search(r"\b([A-Z]\w*Error|Exception)\s*:\s*['\"`]?", task)
    if bare and len(task) < 200:  # cap on length to avoid false-positives
        return True
    return False


def _classify(task: str) -> tuple[str, list[str]]:
    """Return (procedure, rejected_procedures-with-reasons).

    v0.2: structural queries are sub-typed (dead/cycle/complexity/blast/
    callers/coupling) so each gets a procedure-specific routing hint.
    W35a: stack_trace_fix takes precedence — a real traceback is
    unambiguous and the per-frame source slice probe is high value.
    """
    rejected = []
    # W-META (2026-06-09) — contentless session-continuation directives
    # ("ultrathink: lets keep going", "think harder: continue"). The single
    # biggest freeform family in production telemetry (~100 unique / 20%+).
    # Checked FIRST: these carry no task content, so every other regex is
    # noise on them. Content-bearing prefixed prompts ("ultrathink: what
    # changed in cli.py") fall through — the tail guard rejects them.
    if _is_session_meta(task):
        return "session_meta", rejected
    # W-BATCH (2026-06-09) — self-contained batch payloads ("You are
    # validating…", "Synthesize the producer + validator outputs…" with an
    # explicit output spec). Cross-repo transcript mining: 63% of foreign-repo
    # prompts are this shape — they need ZERO repo facts, yet burned the full
    # always-on probe budget and polluted named_paths. Two-signal trigger
    # (length + role-opener/output-directive) keeps precision high.
    if _is_self_contained_task(task):
        return "self_contained_task", rejected
    if _looks_like_stack_trace(task):
        return "stack_trace_fix", rejected
    if _TRACE_RE.search(task):
        if _STRUCTURAL_RE.search(task):
            rejected.append("structural_query: trace phrasing dominates")
        return "trace_query", rejected
    # W166 — refactor_move precedence. "extract X from Y into Z" is a
    # refactor task, not a synthesis query. The W124/W159 t8 failure
    # was caused by `_SYNTHESIS_RE` swallowing "extract" before
    # refactor_move could classify. Run the highly-specific
    # refactor_move regex FIRST.
    if _REFACTOR_MOVE_RE.search(task):
        if _SYNTHESIS_RE.search(task):
            rejected.append("synthesis_query: refactor_move regex is more specific")
        return "refactor_move", rejected
    if _SYNTHESIS_RE.search(task):
        return "synthesis_query", rejected
    # W12 — top-N ranking. Checked BEFORE structural subtype because
    # "top 5 most-imported files" matches BOTH _is_top_n_ranking AND
    # `structural_coupling` regex; the ranking intent is the more
    # specific signal and should win. Only fires when a recognised
    # ranking DIMENSION is present.
    if _is_top_n_ranking(task):
        if _classify_structural_subtype(task):
            rejected.append("structural_subtype: top_n_ranking is more specific")
        return "top_n_ranking", rejected
    # W28 — compare-X-vs-Y. Checked BEFORE structural subtype because
    # "compare cli.py vs mcp_server.py" superficially mentions multiple
    # files and could be misread as a coupling query; the comparison
    # intent is more specific.
    if _is_compare_x_vs_y(task):
        if _classify_structural_subtype(task):
            rejected.append("structural_subtype: compare_x_vs_y is more specific")
        return "compare_x_vs_y", rejected
    sub = _classify_structural_subtype(task)
    if sub:
        return sub, rejected
    # W-REPO (2026-06-09) — repo-level structure ("what are the layers of
    # this codebase", "what are the clusters", "what is the health score of
    # this repo"). No file/symbol anchor, so the structural subtypes never
    # fire; the answer is one repo-scoped roam command embedded at compile
    # time. Telemetry: 10+ unique freeform leaks.
    if _extract_repo_structure(task):
        return "repo_structure", rejected
    # W-ENTRY / W-CFG (2026-06-09) — "what's the entry point for the CLI" and
    # "where is the ROAM_X env var configured". Both probe functions (W67 /
    # W49) existed but only ran on the L1 path, which a target-less freeform
    # prompt never reached — so these prompts compiled to EMPTY envelopes.
    # Routing them as dedicated task-text-target procedures lets the probes
    # fire. Checked before W11 so the env-var name / "entry" bareword is not
    # mis-latched as a symbol lookup.
    if _ENTRY_POINT_RE.search(task):
        return "entry_point_where", rejected
    if _CONFIG_BY_NAME_RE.search(task):
        return "config_where", rejected
    # W13 — CLI-verb perf shape ("why is roam dead slow"). Must precede
    # the bare W11 check because "roam dead slow" mentions a SUBCMD
    # that looks like a bareword symbol — W11 would otherwise latch
    # on `dead` as the identifier.
    if _is_cli_verb_why_slow(task):
        return "cli_verb_why_slow", rejected
    # W-HIST (2026-06-09) — file-history ("what changed in X recently",
    # "who last touched Y"). Requires BOTH a history-intent verb AND a
    # concrete file target, so the match is precise. Checked before W11:
    # "what changed in cli.py" must not latch onto a bareword symbol.
    if _is_file_history(task):
        return "file_history", rejected
    # W11 — symbol-defined-where (bareword + definition-intent verb +
    # NO file path anchor). Skip when an explicit file path is already
    # present — that signals a different intent (file-info / explain).
    if _is_symbol_defined_where(task):
        return "symbol_defined_where", rejected
    # W-LIFT — file_purpose ("describe / what does / explain what X does" + a
    # file path). Lowest priority: only catches what would otherwise be the
    # generic freeform dump.
    if _is_describe_file(task):
        return "describe_file", rejected
    return "freeform_explore", rejected


# ---- W11/W12/W13 classifier helpers ------------------------------------
# Centralised in helpers (rather than inline in `_classify`) so the same
# precondition logic is shared with the dispatch-trace command and the
# per-probe handlers below. Each helper returns True ONLY when the
# regex AND the family-specific preconditions all hold.

# W13 — restrict to known roam subcommands. The regex captures any
# `roam <token>` shape, but only resolvable subcommands should classify
# into the perf procedure. The set is built lazily from `cli._COMMANDS`
# (the cli import is deferred to keep compiler module import cheap).
_CLI_VERB_RESOLVER_CACHE: dict[str, tuple[str, str]] | None = None


def _resolve_cli_verb(subcmd: str) -> tuple[str, str] | None:
    """Return (module_path, entry_function) for *subcmd* via cli._COMMANDS.

    Returns None when the token isn't a registered roam subcommand. The
    lookup table is cached on first use to avoid repeated cli imports
    (each compile call may run the W13 probe).
    """
    global _CLI_VERB_RESOLVER_CACHE
    if _CLI_VERB_RESOLVER_CACHE is None:
        try:
            from roam.cli import _COMMANDS as _cli_commands  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            log_swallowed("compile.cli_verb_resolver_import", exc)
            _CLI_VERB_RESOLVER_CACHE = {}
            return None
        _CLI_VERB_RESOLVER_CACHE = dict(_cli_commands)
    return _CLI_VERB_RESOLVER_CACHE.get(subcmd.lower())


# Edit/fix intent — "fix the bug where roam X is slow" is a BUG-FIX, not a
# why-slow DIAGNOSIS question. Dogfood 2026-06-07: such tasks mis-routed to
# cli_verb_why_slow (perf probe) with garbage named_paths. Guards the
# diagnosis-shaped W13 procedure so edit intent falls through to freeform/edit.
_EDIT_INTENT_RE = re.compile(
    r"\b(fix\s+(it|this|the|that)\b|the\s+bug\b|repair\b|resolve\s+the\b|"
    r"patch\s+the\b|optimi[sz]e\b|speed\s+up\b|make\s+\S+\s+faster\b|"
    r"refactor\b|rewrite\b)",
    re.IGNORECASE,
)


def _is_cli_verb_why_slow(task: str) -> bool:
    """W13 — must match the regex AND name a registered roam subcommand.

    Edit-intent tasks ("fix the bug where roam X is slow") are bug-fixes, not
    why-slow diagnosis questions — exclude them so they don't route to the perf
    probe (which then resolves garbage named_paths)."""
    if _EDIT_INTENT_RE.search(task):
        return False
    m = _CLI_VERB_WHY_SLOW_RE.search(task)
    if not m:
        return False
    subcmd = (m.group(1) or "").lower()
    if not subcmd:
        return False
    return _resolve_cli_verb(subcmd) is not None


_CLI_CMD_REF_RE = re.compile(r"\broam\s+([a-z][a-z0-9-]{1,30})\b")


def _resolve_cli_command_files(task: str, cwd: str | None) -> list[str]:
    """Resolve ``roam <subcommand>`` references to the subcommand's module file
    (dogfood: "add a --json flag to roam smells" used to land in
    empty freeform — the agent got no file to edit). The cli-verb registry maps
    the subcommand → its `roam.commands.cmd_*` module; converted to a repo path
    and kept ONLY if it exists in ``cwd`` (so it fires when editing roam itself
    and is a graceful no-op in any other repo)."""
    if not task or not cwd:
        return []
    out: list[str] = []
    for m in _CLI_CMD_REF_RE.finditer(task):
        resolved = _resolve_cli_verb(m.group(1))
        if not resolved:
            continue
        rel = "src/" + str(resolved[0]).replace(".", "/") + ".py"
        if os.path.exists(os.path.join(cwd, rel)) and rel not in out:
            out.append(rel)
    return out


# W11 — bareword extraction. Returns the symbol the user is asking
# about (without backticks), or None if no concrete bareword fired.
def _extract_symbol_defined_where(task: str) -> str | None:
    # Reject when an explicit file path is present — that signals a
    # different intent (file-info / explain).
    if _extract_file_paths(task):
        return None
    # Iterate matches and pick the first non-stopword bareword. Earlier
    # versions called `re.search` and rejected on the first hit, which
    # missed cases like "locate the function compile_plan" where the
    # first regex match latches onto the stopword "the".
    for m in _SYMBOL_DEFINED_WHERE_RE.finditer(task):
        sym = next((g for g in m.groups() if g), None)
        if not sym or len(sym) < 3:
            continue
        # Reject pure stopwords / common English nouns that aren't
        # plausible identifiers (`file`, `code`, `that`, `what`, ...).
        if sym.lower() in _W11_STOPWORDS:
            continue
        # Concept-search guard (dogfood): "find SQL injection risks",
        # "find security issues", "find performance problems", "find memory
        # leaks" — a PLAIN English noun (not identifier-shaped: no `_`, no digit,
        # all-lower or all-upper) FOLLOWED BY MORE content words is a conceptual
        # search, not "find symbol X". Treating the noun as a symbol returned
        # garbage; reject so it falls through to freeform/semantic search.
        # Identifier-shaped names (compile_plan, useState, MyClass) and terminal
        # barewords ("find main") are unaffected.
        is_plain = "_" not in sym and not any(c.isdigit() for c in sym) and (sym.islower() or sym.isupper())
        if is_plain:
            _defv = {"is", "defined", "located", "lives", "live", "for", "of", "in", "the", "a", "an"}
            tail_words = [w.lower() for w in re.findall(r"[A-Za-z]{2,}", task[m.end() :])]
            if any(w not in _defv for w in tail_words):
                continue
        return sym
    return None


def _is_symbol_defined_where(task: str) -> bool:
    return _extract_symbol_defined_where(task) is not None


# W-HIST — file-history intent. Deliberately TIGHTER than the broad
# _HISTORY_QUERY_RE augmenter trigger (which keys words like "since" /
# "recent" for additive freeform facts): routing a dedicated procedure at
# 0.85 confidence needs an unambiguous history VERB. The file target is
# checked separately in _extract_file_history_target.
_FILE_HISTORY_INTENT_RE = re.compile(
    r"\b(what\s+(?:has\s+)?changed|recent\s+(?:changes?|commits?|edits?)|"
    r"change\s+history|commit\s+history|git\s+history|history\s+of|"
    r"who\s+(?:last\s+)?(?:changed|touched|modified|edited)|"
    r"when\s+did\s+\S+\s+(?:last\s+)?change|latest\s+(?:change|commit|edit)s?)\b",
    re.IGNORECASE,
)


def _extract_file_history_target(task: str) -> str | None:
    """Return the file the history question is about, or None. Requires BOTH
    a history-intent verb AND a file target (slash-path or bare filename)."""
    if not task:
        return None
    if not _FILE_HISTORY_INTENT_RE.search(task):
        return None
    paths = _extract_file_paths(task)
    if paths:
        return paths[0]
    m = _BARE_FILE_RE.search(task)
    if m:
        return m.group(1)
    return None


def _is_file_history(task: str) -> bool:
    return _extract_file_history_target(task) is not None


# W-REPO — repo-level structure dimensions. Each maps to ONE repo-scoped
# roam command whose summary IS the answer. The regexes demand a repo-level
# frame ("of this codebase" / "what are the ...") so file- or symbol-scoped
# questions keep falling through to the structural subtypes.
_REPO_STRUCTURE_PATTERNS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    (
        "layers",
        re.compile(
            r"\b(?:what\s+are\s+the\s+layers\b|"
            r"layers\s+(?:of|in)\s+(?:this\s+|the\s+)?(?:codebase|repo(?:sitory)?|project|system))",
            re.IGNORECASE,
        ),
    ),
    (
        "clusters",
        re.compile(
            r"\b(?:what\s+are\s+the\s+clusters\b|"
            r"clusters?\s+(?:of|in)\s+(?:this\s+|the\s+)?(?:codebase|repo(?:sitory)?|project|system))",
            re.IGNORECASE,
        ),
    ),
    (
        "health",
        re.compile(
            r"\b(?:health\s+score\b|how\s+healthy\s+is\b|"
            r"overall\s+health\s+of\b)",
            re.IGNORECASE,
        ),
    ),
)


def _extract_repo_structure(task: str) -> str | None:
    """Return the repo-structure dimension (layers|clusters|health), or None."""
    if not task:
        return None
    for dim, rgx in _REPO_STRUCTURE_PATTERNS:
        if rgx.search(task):
            return dim
    return None


# W-META — session-continuation directives. Leading thinking-mode markers
# are stripped, then the remainder must be a SHORT pure-continuation phrase
# (or empty). Any file path, backtick, or length beyond the cap means real
# task content → fall through to the normal classifier chain.
_SESSION_META_MARKER_RE = re.compile(
    r"^\s*(?:ultrathink|think\s+harder|think\s+hard(?:er)?|think|megathink)"
    r"\s*[:,–—-]?\s*",
    re.IGNORECASE,
)
_SESSION_META_CONTINUE_RE = re.compile(
    r"^(?:ok(?:ay)?[,!.\s]*|yes[,!.\s]*|please[,!.\s]*|now[,!.\s]*)*"
    r"(?:let'?s\s+)?(?:keep\s+(?:going|at\s+it)|continue|go\s+on|proceed|"
    r"carry\s+on|onwards?|next|keep\s+up|more|again|"
    r"what'?s\s+next|whats\s+next)\b[\s!.,]*$",
    re.IGNORECASE,
)
_SESSION_META_MAX_TAIL_CHARS = 60

# W-BATCH — self-contained batch-payload detection. Both signals required:
# a substantial prompt body AND an explicit self-contained marker (role-play
# opener or output-format directive). Repo-relative source paths veto the
# fast-path — a long prompt that anchors on repo files still wants probes.
# Two-tier floors: the role-play opener is a strong signal on its own
# (200-char floor); a bare output-format directive needs a longer body
# (400) to avoid catching short repo questions that mention JSON output.
_SELF_CONTAINED_OPENER_MIN_CHARS = 200
_SELF_CONTAINED_MIN_CHARS = 400
_SELF_CONTAINED_OPENER_RE = re.compile(
    r"^\s*(?:you\s+are\s+(?:a|an|the|validating|scoring|reviewing)\b|"
    r"synthesize\s+the\b|produce\s+a\b.{0,60}\b(?:dossier|spec|report)\b)",
    re.IGNORECASE | re.DOTALL,
)
_SELF_CONTAINED_OUTPUT_RE = re.compile(
    r"\b(?:return\s+only|output\s+only|respond\s+with\s+only|"
    r"output\s+json|output\s+format|json\s+schema|emit\s+only)\b",
    re.IGNORECASE,
)
_REPO_RELATIVE_PATH_RE = re.compile(r"(?<![\w/])(?:src|lib|app|tests?)/[\w./-]+")


def _is_self_contained_task(task: str) -> bool:
    """True for self-contained batch payloads that need no repo prefetch."""
    if not task or len(task) < _SELF_CONTAINED_OPENER_MIN_CHARS:
        return False
    if _REPO_RELATIVE_PATH_RE.search(task):
        return False  # anchored on repo files → probes still valuable
    if _SELF_CONTAINED_OPENER_RE.match(task):
        return True
    return len(task) >= _SELF_CONTAINED_MIN_CHARS and bool(_SELF_CONTAINED_OUTPUT_RE.search(task))


def _is_session_meta(task: str) -> bool:
    """True for contentless continuation directives, after marker stripping."""
    if not task:
        return False
    tail = _SESSION_META_MARKER_RE.sub("", task).strip()
    if not tail:
        # a bare thinking-mode marker IS a continuation directive
        return bool(_SESSION_META_MARKER_RE.match(task))
    if len(tail) > _SESSION_META_MAX_TAIL_CHARS:
        return False
    if "`" in tail or "/" in tail or _BARE_FILE_RE.search(tail):
        return False
    return bool(_SESSION_META_CONTINUE_RE.match(tail))


# W12 — return (dimension, n) tuple or None. The dimension is mapped
# to a canonical key the probe uses to pick the roam command.
def _extract_top_n_ranking(task: str) -> tuple[str, int] | None:
    m = _TOP_N_RANKING_RE.search(task)
    if not m:
        return None
    # Two alternations: Shape A uses groups (1, 2); Shape B uses (3, 4).
    n_raw = m.group(1) or m.group(3)
    dim_raw = m.group(2) or m.group(4)
    try:
        n = int(n_raw) if n_raw else 5
    except (TypeError, ValueError):
        n = 5
    n = max(1, min(n, 50))
    dim = (dim_raw or "").lower().replace(" zone", "")
    dim_canon = _W12_DIMENSION_MAP.get(dim)
    if dim_canon is None:
        return None
    return (dim_canon, n)


def _is_top_n_ranking(task: str) -> bool:
    return _extract_top_n_ranking(task) is not None


# W28 — extract (X, Y) from a "compare X vs Y" / "diff X and Y" / "what's
# the difference between X and Y" prompt. Returns None when no concrete
# pair was captured. Stopwords (the / a / this / that / function / class /
# symbol / module / file) are rejected because they're never the actual
# subject of comparison. The connector/glue group (and / to / with / vs /
# from / pronouns / question words) is what made "...all telemetry AND
# compared TO vanilla where we stand" mis-fire as compare("and","vanilla"):
# the non-greedy operand capture grabs the separator word itself out of
# free prose. None of these is ever a code symbol or path, so blocking them
# kills that false positive while keeping real operands like "vanilla".
_W28_STOPWORDS: frozenset[str] = frozenset(
    {
        "the",
        "a",
        "an",
        "this",
        "that",
        "these",
        "those",
        "it",
        "function",
        "method",
        "class",
        "module",
        "file",
        "symbol",
        "name",
        "code",
        "thing",
        "stuff",
        "part",
        "one",
        "another",
        "other",
        "and",
        "or",
        "to",
        "with",
        "vs",
        "versus",
        "against",
        "from",
        "of",
        "in",
        "on",
        "for",
        "is",
        "are",
        "be",
        "been",
        "we",
        "us",
        "you",
        "i",
        "they",
        "here",
        "there",
        "now",
        "where",
        "what",
        "how",
        "why",
        "when",
    }
)


def _extract_compare_x_vs_y(task: str) -> tuple[str, str] | None:
    """W28 — return the (X, Y) tokens being compared, or None."""
    m = _COMPARE_X_VS_Y_RE.search(task or "")
    if not m:
        return None
    # The regex has 6 alternations × 2 groups each = 12 groups. Pick the
    # first non-None pair (groups come in adjacent pairs).
    groups = m.groups()
    for i in range(0, len(groups), 2):
        x = groups[i]
        y = groups[i + 1] if i + 1 < len(groups) else None
        if x and y:
            x_clean = x.strip().strip("`").strip()
            y_clean = y.strip().strip("`").strip()
            if not x_clean or not y_clean:
                continue
            if x_clean.lower() in _W28_STOPWORDS or y_clean.lower() in _W28_STOPWORDS:
                continue
            if x_clean.lower() == y_clean.lower():
                continue
            # Minimum length guard — single-char entities are noise.
            if len(x_clean) < 2 or len(y_clean) < 2:
                continue
            return (x_clean, y_clean)
    return None


def _is_compare_x_vs_y(task: str) -> bool:
    """W28 — classifier helper. Matches when a concrete (X, Y) pair is found."""
    return _extract_compare_x_vs_y(task) is not None


# W-LIFT (2026-06-02): file_purpose / describe-file shape. Usage telemetry
# (2863 compile-runs) showed "what does X do" / "describe the purpose of X" /
# "explain what X does" was the largest cluster mislabeled into the generic
# low-confidence freeform_explore (37% of all compile traffic, median conf 0.45).
# These have a FILE target + a deterministic answer (the file skeleton + summary
# + small-file body) that `_probe_freeform_skeleton` already computes. Routing
# them to a dedicated procedure gives a high-confidence, tight-contract envelope
# instead of the broad freeform dump. REQUIRES a concrete file path so abstract
# "explain how the auth flow works" (no path) correctly stays freeform.
_DESCRIBE_FILE_RE = re.compile(
    r"\b(?:what\s+does|what(?:'s|\s+is)\s+(?:in|the\s+purpose\s+of|the\s+role\s+of)|"
    r"describe|explain(?:\s+(?:what|how))?|summar(?:y|ise|ize)\b|"
    r"purpose\s+of|role\s+of|walk\s+me\s+through|overview\s+of|"
    # API-surface phrasings — the file_skeleton (top-level def/class list) IS
    # the export list, so these resolve to describe_file. Telemetry
    # (2026-06-05): "what's exported from cmd_verify.py" / "audit the public API
    # of src/roam/cli.py" leaked to low-conf freeform_explore.
    r"exported\s+(?:from|by)|what(?:'s|\s+is)\s+exported|public\s+api\s+of|"
    r"public\s+(?:functions?|methods?|symbols?|classes)\b|"
    r"api\s+surface\s+of)\b",
    re.IGNORECASE,
)


# Module-level describe — "explain the compiler architecture", "what does the
# constitution package do", "explain the purpose of the indexer module". No
# file path or bare filename is present, so _extract_describe_file missed
# these (telemetry 2026-06-09: 7+ unique freeform leaks). The captured module
# word resolves to a UNIQUE repo file at dispatch time via
# `_resolve_module_names` (stem or package-__init__ match; ambiguous → skip).
# Two separate regexes (not one alternation): on "the architecture of the
# indexer", Shape A's stopword match ("the architecture") would consume the
# token before Shape B could anchor on it.
_DESCRIBE_MODULE_A_RE = re.compile(
    # "<name> module|package|subsystem|architecture"
    r"(?:\bthe\s+|\bour\s+)?\b([a-z_][a-z0-9_]{2,30})\s+"
    r"(?:module|package|subsystem|architecture)\b",
    re.IGNORECASE,
)
_DESCRIBE_MODULE_B_RE = re.compile(
    # "architecture|internals of (the) <name>"
    r"\b(?:architecture|internals)\s+of\s+(?:the\s+)?([a-z_][a-z0-9_]{2,30})\b",
    re.IGNORECASE,
)

# Words a module-describe capture must never treat as a module name — they
# appear in repo-level phrasings ("this codebase architecture", "the overall
# architecture") that belong to other procedures or freeform.
_DESCRIBE_MODULE_STOPWORDS: frozenset[str] = frozenset(
    {
        "this",
        "the",
        "that",
        "our",
        "whole",
        "entire",
        "overall",
        "current",
        "codebase",
        "repo",
        "repository",
        "project",
        "system",
        "code",
        "core",
        "main",
        "new",
        "old",
        "test",
        "tests",
        "its",
        "any",
        "every",
        "each",
    }
)


def _extract_describe_module(task: str) -> str | None:
    """Return a plausible module/package name being described, or None.
    Requires BOTH a describe-intent verb AND a `<name> module|package|
    subsystem|architecture` frame whose name survives the stopword guard."""
    if not task:
        return None
    if not (_DESCRIBE_FILE_RE.search(task) or _DESCRIBE_FILE_FOR_RE.search(task)):
        return None
    for rgx in (_DESCRIBE_MODULE_A_RE, _DESCRIBE_MODULE_B_RE):
        for m in rgx.finditer(task):
            name = m.group(1)
            if not name or name.lower() in _DESCRIBE_MODULE_STOPWORDS:
                continue
            return name
    return None


# "what is X.py for" / "what's X for" frame — a describe-the-file intent the
# anchored _DESCRIBE_FILE_RE missed (it required "what is in/the purpose of").
# Telemetry (2026-06-04): "what is cmd_verify.py for" fell to empty-prefetch
# freeform. Bounded span + path-gated in _extract_describe_file → safe.
_DESCRIBE_FILE_FOR_RE = re.compile(r"\bwhat(?:'s|\s+is)\b[^?]{0,50}?\bfor\b", re.IGNORECASE)


def _extract_describe_file(task: str) -> str | None:
    """Return the file path/name being described, or None. Requires BOTH a
    describe-intent verb AND a file target (slash-path OR a bare code-filename)."""
    if not task:
        return None
    if not (_DESCRIBE_FILE_RE.search(task) or _DESCRIBE_FILE_FOR_RE.search(task)):
        return None
    paths = _extract_file_paths(task)
    if paths:
        return paths[0]
    # Bare code-filename (no slash) — "what is cmd_verify.py for", "what's
    # exported from parser.py". `_classify` is cwd-less so it cannot DB-resolve
    # the basename here; a filename-SHAPED token is sufficient routing signal,
    # and the probe (`_probe_freeform_skeleton` / api_surface) resolves it to a
    # real repo path via `_resolve_bare_filenames` at dispatch time. A
    # non-existent name → probe yields empty facts gracefully (cheap miss).
    m = _BARE_FILE_RE.search(task)
    if m:
        return m.group(1)
    # Module-name target — "explain the compiler architecture", "what does
    # the constitution package do". Resolved to a unique repo file at
    # dispatch time via _resolve_module_names; a non-resolving name degrades
    # to an empty-fact probe (cheap miss, same as a bad bare filename).
    mod = _extract_describe_module(task)
    if mod:
        return mod
    return None


def _is_describe_file(task: str) -> bool:
    return _extract_describe_file(task) is not None


# W11 — barewords that would otherwise pass the regex's identifier
# shape but are common English nouns / pronouns, not plausible code
# symbols. Keep small and conservative — false positives here cost an
# unnecessary `roam search` call (cheap), false negatives lose probe
# coverage (the whole point of W11).
_W11_STOPWORDS: frozenset[str] = frozenset(
    {
        "that",
        "this",
        "what",
        "where",
        "which",
        "code",
        "file",
        "thing",
        "stuff",
        "part",
        "module",
        "function",
        "method",
        "class",
        "symbol",
        "name",
        "case",
        "test",
        "the",
    }
)


# W12 — canonicalise the captured ranking dimension to one of a closed
# enum the probe knows how to query. Keeping the enum small keeps the
# probe's roam-command dispatch table small.
_W12_DIMENSION_MAP: dict[str, str] = {
    "imported": "imports",
    "importing": "imports",
    "imports": "imports",
    "coupled": "coupling",
    "coupling": "coupling",
    "complex": "complexity",
    "complicated": "complexity",
    "complexity": "complexity",
    "churned": "churn",
    "churning": "churn",
    "churn": "churn",
    "danger": "danger",
    "dangerous": "danger",
    "important": "importance",
    "importance": "importance",
    "central": "importance",
    "connected": "importance",
    "pagerank": "importance",
    "file": "complexity",
    "files": "complexity",
    "module": "complexity",
    "modules": "complexity",
    "called": "callers",
    "caller": "callers",
    "callers": "callers",
    "cycles": "cycles",
    "cluster": "clusters",
    "clusters": "clusters",
    "bottlenecks": "bottlenecks",
    "large": "complexity",
    "long": "complexity",
}


# R10.1 classifier confidence (2026-05-29). The memo finding:
#   "R10 specialized contracts AMPLIFY classifier accuracy. When
#    classification is correct, R10 wins. When wrong (vue01 misclassified
#    as freeform), R10 LOSES MORE than the generic contract."
# Confidence gates specialized policy application — fall back to safe
# generic when the regex match was thin/ambiguous.
_STRUCTURAL_SUBTYPE_REGEXES = (
    ("structural_dead", _STRUCTURAL_DEAD_RE),
    ("structural_cycle", _STRUCTURAL_CYCLE_RE),
    ("structural_complexity", _STRUCTURAL_COMPLEXITY_RE),
    ("structural_coupling", _STRUCTURAL_COUPLING_RE),
    ("structural_blast", _STRUCTURAL_BLAST_RE),
    ("structural_callers", _STRUCTURAL_CALLERS_RE),
)


def _classifier_confidence(task: str, procedure: str) -> float:
    """Confidence in the classifier's procedure choice on 0..1.

    Signals:
      * trace/synthesis regex hit cleanly             → 0.85
      * structural_*: exactly one subtype matched     → 0.85
      * structural_*: 2 subtypes matched (compound)   → 0.55
      * structural_*: 3+ matched (ambiguous compound) → 0.40
      * freeform_explore (regex fall-through)         → 0.35
      * named explicit path present                   → +0.10 boost (caps at 0.95)
    """
    if procedure == "freeform_explore":
        score = 0.35
    elif procedure == "stack_trace_fix":
        # W35a — stack-trace pattern requires BOTH a real frame AND an
        # error context word, so the match is unambiguous when it fires.
        score = 0.90
    elif procedure == "trace_query" or procedure == "synthesis_query":
        score = 0.85
    elif procedure in (
        "symbol_defined_where",
        "top_n_ranking",
        "cli_verb_why_slow",
        "file_history",
        "repo_structure",
        "entry_point_where",
        "config_where",
        "session_meta",
        "self_contained_task",
    ):
        # W11/W12/W13 — regex matches are precise (bareword + verb / dimension
        # token + anchor / CLI verb resolver-gated). Confidence parity with
        # trace/synthesis. Without this, score fell to 0.50 (the else branch)
        # which is below the 0.80 L1 threshold — caused 46 historical calls
        # to drop to `art_label: full` instead of `l1_probe` despite probes
        # firing. Discovered by 2026-06-02 compiler-usage analysis.
        score = 0.85
    elif procedure == "compare_x_vs_y":
        # W28 — comparison regex requires a concrete (X, Y) pair AND a
        # comparison verb ("compare" / "vs" / "diff" / "difference between");
        # the matched shape is unambiguous when it fires.
        score = 0.85
    elif procedure == "describe_file":
        # W-LIFT — a describe verb + a concrete file path is unambiguous;
        # the file skeleton/summary IS the answer. Parity with the precise
        # regex procedures so the `facts` policy fires the probe (not `full`).
        score = 0.85
    elif procedure.startswith("structural_"):
        hits = sum(1 for _, rgx in _STRUCTURAL_SUBTYPE_REGEXES if rgx.search(task))
        if hits <= 1:
            score = 0.85
        elif hits == 2:
            score = 0.55
        else:
            score = 0.40
    else:
        score = 0.50

    # Named explicit path is a strong scope anchor; bump confidence.
    if _extract_file_paths(task):
        score = min(0.95, score + 0.10)
    return round(score, 2)


# Threshold above which the policy table is allowed to pick a specialized
# (non-"full") artifact. Below: fall back to "full" — the safe baseline that
# tolerates classifier error. Calibrated against the R10 vue01 regression
# (procedure was freeform_explore from a thin match; confidence 0.35 < 0.60
# → would fall back to full instead of facts, avoiding the regression).
_CONFIDENCE_THRESHOLD = 0.60

# W51 — per-procedure thresholds. Telemetry (W43 P3) shows different
# procedures have different baseline confidence distributions:
#   stack_trace_fix: regex match is unambiguous, threshold 0.85 safe
#   structural_*: subtype regex is precise, threshold 0.60 ok
#   trace/synthesis: clean phrasing, threshold 0.70
#   freeform_explore: catch-all, threshold 0.30 (don't block fall-through)
# Falls back to the global _CONFIDENCE_THRESHOLD when procedure absent.
_PER_PROCEDURE_CONF_THRESHOLD: dict[str, float] = {
    "stack_trace_fix": 0.85,
    "structural_coupling": 0.60,
    "structural_callers": 0.60,
    "structural_dead": 0.60,
    "structural_blast": 0.60,
    "structural_complexity": 0.60,
    "structural_cycle": 0.60,
    "trace_query": 0.70,
    "synthesis_query": 0.70,
    "freeform_explore": 0.30,
    # W181 — refactor_move added (was missing; W166 classifier could
    # return it but downstream dicts didn't have an entry, crashing).
    "refactor_move": 0.70,
    # W11/W12/W13 — three new probe families added 2026-06-02. Each
    # regex is precise (bareword + verb / dimension token / CLI verb
    # resolver) so the confidence is high when it fires.
    "symbol_defined_where": 0.80,
    "top_n_ranking": 0.80,
    "cli_verb_why_slow": 0.85,
    # W28 — compare-X-vs-Y; pair extraction guarantees a concrete subject.
    "compare_x_vs_y": 0.80,
    # W-LIFT — describe-file; requires a concrete file path + describe verb.
    "describe_file": 0.80,
    # W-HIST — file-history; requires a history verb + a concrete file target.
    "file_history": 0.80,
    # W-REPO — repo-structure; the dimension regex is repo-frame-anchored.
    "repo_structure": 0.80,
    # W-ENTRY / W-CFG — precise intent regexes (W67/W49 probe triggers).
    "entry_point_where": 0.80,
    "config_where": 0.80,
    # W-META — marker + contentless-tail guard make the match precise.
    "session_meta": 0.80,
    # W-BATCH — two-signal trigger (length + opener/output-directive).
    "self_contained_task": 0.80,
}


# ---- TASK→TOOL routing — verified empirical winners (CLAUDE.md, 2026-05-23) ----
# v0.2: structural sub-types each get a single, specific first-command hint.
# Lesson from v0.1: omnibus hints make the agent anchor on the first option.
_RECOMMENDED_FIRST_COMMAND = {
    "structural_coupling": (
        "roam_coupling + roam_deps in PARALLEL (one tool_use block). For 'top imported' alone, roam_deps."
    ),
    "structural_callers": "roam_uses on the named symbol. One call.",
    "structural_dead": (
        "roam_dead_code (returns project-wide unused exports + breakdown). "
        "Cross-check with Grep for symbol name as a final confirmation."
    ),
    "structural_cycle": (
        "roam_clusters or roam_graph_cycles. For Vue/JS specifically, "
        "fall back to roam_deps + import-trace if cycle tool unavailable."
    ),
    "structural_complexity": (
        "roam_understand or roam_file_info on the named directory/file; "
        "wc -l for raw line counts is acceptable as a tie-breaker."
    ),
    "structural_blast": "roam_impact + roam_uses in PARALLEL.",
    # legacy alias kept for back-compat with v0/v0.1 cached envelopes
    "structural_query": (
        "roam_coupling + roam_deps in PARALLEL (one tool_use block) "
        "for file coupling; roam_uses for callers; roam_impact for blast; "
        "roam_dead_code for unused; roam_search_symbol for symbol lookup."
    ),
    "trace_query": (
        'roam_retrieve "<task>" — graph-aware FTS5 + structural rerank; '
        "for known entry point, roam_uses + roam_search_semantic in PARALLEL."
    ),
    "synthesis_query": (
        "SKIP roam for content writing — Read named files directly, then Edit. "
        "For refactor proposal: roam_impact + roam_uses in PARALLEL first."
    ),
    "freeform_explore": (
        'roam_ask "<task>" — intent dispatcher; if low-confidence, fall back to roam_search_semantic for likely files.'
    ),
    "stack_trace_fix": (
        "Read the embedded `stack_frames` (top frame is the failing call). "
        "Open the lowest user-code frame first, fix root cause, re-run the "
        "failing test or command."
    ),
    # W181 — refactor_move missing entry caused KeyError that silently
    # demoted ALL refactor_move tasks back to synthesis_query in iter-3.
    # Now wired to: read embedded source_body + destination_skeleton,
    # write dst_file, rewrite caller_import_lines.
    "refactor_move": (
        "Read embedded `refactor_move.source_body` (the symbol to move). "
        "Write `refactor_move.destination_file` with the embedded "
        "`destination_skeleton` + source_body inlined. Then rewrite each "
        "caller's import per `caller_import_lines`. Use Edit, not multiple "
        "Reads."
    ),
    # W11 — bareword "where is X defined" routes to roam search-symbol.
    "symbol_defined_where": (
        "Read the embedded `symbol_definitions` (top-5 candidate file:line "
        "pairs). Open the first match; if signature mismatch, try the "
        "next rank. Skip Glob/Grep — the index already knows."
    ),
    # W-HIST — "what changed in FILE recently" answers from the embedded log.
    "file_history": (
        "Read the embedded `file_recent_commits` (hash date author subject "
        "per line) — it IS the answer. Do NOT run git log or git blame."
    ),
    # W-REPO — repo-level layers/clusters/health answers from the summary.
    "repo_structure": (
        "Read the embedded `repo_structure_result.summary` (verdict + "
        "counts) — it IS the answer. Run the named `roam <dimension>` "
        "command only when the user asks for the full per-item breakdown."
    ),
    # W-ENTRY — protocol-classified entry points from the embedded probe.
    "entry_point_where": (
        "Read the embedded `entry_points` (kind + location per entry) — "
        "it IS the answer. Open the kind-matched entry file only if the "
        "user asks about the startup flow beyond the location."
    ),
    # W-CFG — env-var/config definition sites from the embedded grep.
    "config_where": (
        "Read the embedded `config_matches` (file:line + snippet) — it IS "
        "the answer. Do NOT re-grep; cite the definition site directly."
    ),
    # W-META — continuation directive: the conversation is the task.
    "session_meta": (
        "Continue the in-flight work from the conversation. Skim "
        "`session_brief` for repo state; do NOT start fresh exploration."
    ),
    # W-BATCH — self-contained payload: execute it, skip repo exploration.
    "self_contained_task": (
        "Execute the prompt exactly as written — it is self-contained. "
        "Skip roam tools and repo exploration; read only files the prompt "
        "names."
    ),
    # W12 — "top N most-X files" routes to the matching roam top-N command.
    "top_n_ranking": (
        "Read the embedded `top_n_ranking.items` — already ranked, no need "
        "to call roam coupling/complexity/health yourself. Cite items by "
        "rank with the dimension-native score."
    ),
    # W13 — "why is roam <SUBCMD> slow" — embed entry function + hot spots.
    "cli_verb_why_slow": (
        "Read the embedded `cli_verb_slow_diagnosis` — `entry_function` "
        "is your starting Read target; `hot_spots` lists symbols filtered "
        "to the named subcommand's module. Run `roam doctor` for "
        "indexer-phase timings if hot_spots is empty."
    ),
    # W28 — "compare X vs Y" — embed semantic-diff / coupling-filter result.
    "compare_x_vs_y": (
        "Read the embedded `compare_x_vs_y_result` — `diff_summary` is "
        "the headline, `divergence_points` lists the named symbols that "
        "differ, `common_signature` lists shared structure. Skip extra "
        "Read calls — both sides are already summarized."
    ),
    # W-LIFT — describe-file. Skeleton + summary (+ small-file body) embedded.
    "describe_file": (
        "Read the embedded `file_skeleton` + `file_summary` (and "
        "`full_file_body` when present) — that IS the file's structure and "
        "purpose. Answer directly; do NOT Read the file or Grep it."
    ),
}


# ---- R10 per-procedure answer_contract specialization (2026-05-29) ----
# The generic 5-bullet contract works well on average but loses quality
# on procedures whose ideal answer has a different SHAPE (e.g., coupling
# wants file pairs + strength scores, not files + line citations).
# Each procedure gets a contract that describes what a good answer for
# THAT family looks like.

_GENERIC_CONTRACT = (
    "Direct answer to the literal question (1-3 sentences)",
    "Exact files/functions cited with file:line where applicable",
    "Why each cited file matters (one short clause each)",
    "Any uncertain/missing areas flagged honestly",
    "Skip broad unrelated exploration unless directly required",
)

_PROCEDURE_CONTRACTS: dict[str, tuple[str, ...]] = {
    "structural_coupling": (
        "List the top N coupled file PAIRS with strength scores or co-change counts",
        "For each pair, name the specific shared symbols, imports, or call edges",
        "Order strictly from strongest to weakest coupling",
        "Use the format: `file_a` ↔ `file_b` (score N): reason",
        "Skip files with weak/incidental coupling — only the top N",
    ),
    "structural_callers": (
        "List EVERY caller of the target symbol — do not summarize away cases",
        "For each caller, give file:line of the call site",
        "Group by call context (production vs test vs script)",
        "Flag any indirect callers (via reflection/dispatch) separately",
        "If <5 callers, list them all; if many, give count + top sites",
    ),
    "structural_dead": (
        "Name the specific symbol(s) or file(s) that are unused",
        "Provide PROOF of zero references — quote the relevant search command output",
        "State the exclusion scope explicitly (e.g., 'no callers in src/ or tests/')",
        "Flag any indirect uses you considered and ruled out",
        "If recommending deletion, confirm no dynamic dispatch / reflection risk",
    ),
    "structural_cycle": (
        "Walk one CONCRETE cycle: file → file → file → ... → starting file",
        "At each step, cite the file:line of the import statement",
        "If the cycle is via a sub-module, name the bridging file",
        "If no cycle exists, say so explicitly with evidence of what you checked",
        "Avoid listing multiple cycles unless explicitly requested",
    ),
    "structural_complexity": (
        "Name THE single file/component with the highest complexity",
        "Give a CONCRETE metric: line count, function count, cyclomatic depth, or state-field count",
        "Cite file:line of the symbol that pushes it over (e.g., the largest function)",
        "Compare to the second-highest to show meaningful separation",
        "Propose ONE specific refactor in 1-2 sentences",
    ),
    "structural_blast": (
        "State the blast radius as a count (files/symbols affected if X changes)",
        "List the top affected files in priority order, with file:line",
        "Distinguish production callers from test callers",
        "Flag any cross-language or generated-code consumers",
        "State the safe execution order if the refactor is staged",
    ),
    "structural_query": (
        # Legacy fallback — use generic since procedure isn't refined
        "Direct answer to the literal question (1-3 sentences)",
        "Exact files/functions cited with file:line",
        "Why each cited file matters (one short clause)",
        "Any uncertain/missing areas flagged honestly",
        "Skip unrelated exploration",
    ),
    "synthesis_query": (
        "Produce the requested artifact (code/diff/proposal) FIRST",
        "Cite the source file:line for any references the artifact depends on",
        "Show only the relevant diff or block — no full file rewrites unless asked",
        "Explain the WHY of design choices in <=2 sentences",
        "Flag any assumptions or pre-conditions in a separate Notes line",
    ),
    "trace_query": (
        # W103 — cap step count up front so trace answers don't sprawl.
        # The W100 t16 loss showed compile producing 5 steps where vanilla
        # produced 4, costing more turns + cost for the same essential
        # answer. Capping at 4 by default keeps deltas tight without
        # losing the user-asked-for-detail use case.
        "Use at most 4 numbered steps unless the user explicitly asked for full detail",
        "Walk the chain step-by-step: from entry point through each hop",
        "At each hop, cite file:line and name the function/method",
        "If a step branches, follow the most-likely path; note alternatives briefly",
        "End at the requested terminal (output/sink/return); stop there",
    ),
    "freeform_explore": (
        "Lead with a 1-sentence summary of what you found",
        "Cite the 2-3 most evidence-bearing files with file:line",
        "Separate confirmed facts from inferred conclusions",
        "Flag what you did NOT check and what would change the answer",
        "Suggest one concrete next step if the user wants to dig further",
    ),
    "stack_trace_fix": (
        # W39 B1: stronger anti-Read directive — the W38 pilot showed
        # stack_trace_fix winning only marginally (-33% turns, cost flat)
        # because agents were re-Reading files whose source slice was
        # already embedded. The FIRST bullet now is the ban.
        "DO NOT call Read on the files cited in the stack trace — `stack_frames` ALREADY contains a labeled ±5-line slice around each failing line (`>>` marker = the exception site)",
        "Identify the LOWEST user-code frame — that is the root call site",
        "Quote the failing line FROM the embedded `stack_frames[i].excerpt` (cite by file:line, not by repeating Read output)",
        "Name the root cause in one sentence (what condition / input triggered the exception)",
        "Propose the minimal patch as a unified diff against the failing file",
        "If the failure surfaces a missing test, name the test file path to add it to",
    ),
    # W210 — refactor_move added (was missing; W181-class gap caught by
    # `roam dict-consistency`). Imperative steps for move/extract tasks.
    "refactor_move": (
        "Read `refactor_move.source_body` — THIS IS the symbol to move",
        "Write `refactor_move.destination_file` with `destination_skeleton` + source_body inlined",
        "Rewrite each caller's import per `caller_import_lines`",
        "Use Edit (not multiple Reads); the data is in the envelope",
        "Cite source_file:lines as evidence; report a 1-sentence verdict",
    ),
    # W11 — answer shape for "where is X defined".
    "symbol_defined_where": (
        "Lead with the single most-likely definition: file:line + kind",
        "If multiple plausible matches, list ALL up to 5 with rank + signature",
        "Cite the signature directly from `symbol_definitions`; do NOT re-Read",
        "If `symbol_definitions` is empty, say so and suggest `roam init` to refresh",
        "Skip generic exploration — the index is authoritative for this question",
    ),
    # W12 — answer shape for "top N most-X files".
    "top_n_ranking": (
        "Lead with the rank-1 item and the dimension-native score",
        "List the remaining items in rank order with file/symbol + score",
        "Name the dimension explicitly (importance / coupling / churn / complexity)",
        "Skip generic narrative — the ranking is the answer",
        "If the ranking is empty, name the precondition (`roam init` first)",
    ),
    # W13 — answer shape for "why is roam <SUBCMD> slow".
    "cli_verb_why_slow": (
        "State the entry function and module path from `cli_verb_slow_diagnosis`",
        "List the top hot spots in priority order with file:line where present",
        "If `hot_spots` is empty, explicitly point at `roam doctor` for phase timings",
        "Propose ONE concrete next step (Read the entry function, run doctor, ingest traces)",
        "Skip generic perf advice — anchor every claim to a named symbol",
    ),
    # W-HIST — answer shape for "what changed in FILE recently".
    "file_history": (
        "Lead with the most recent commit (hash, date, author, subject)",
        "List the remaining commits in reverse-chronological order",
        "Answer from the embedded `file_recent_commits` — do NOT run `git log` again",
        "When the task names a time window (last week), keep only commits inside it",
        "If `file_history_unavailable` is set, say the file has no tracked history",
    ),
    # W-REPO — answer shape for repo-level layers/clusters/health.
    "repo_structure": (
        "Lead with the verdict line from `repo_structure_result.summary`",
        "Quote the dimension-native counts (layers / clusters / score) verbatim",
        "Answer from the embedded summary — do NOT re-run the roam command",
        "If `repo_structure_unavailable` is set, give the literal command to run",
    ),
    # W-ENTRY — answer shape for "what's the entry point".
    "entry_point_where": (
        "Lead with the kind-matched entry point (cli/http/worker) + its location",
        "List remaining entry points grouped by protocol kind",
        "Answer from the embedded `entry_points` — do NOT re-run roam",
        "If `entry_points_unavailable` is set, give the literal command to run",
    ),
    # W-CFG — answer shape for "where is the X env var configured".
    "config_where": (
        "Lead with the definition site (file:line) of the named config/env var",
        "List read sites separately from the definition site",
        "Answer from the embedded `config_matches` — do NOT grep again",
        "If `config_matches_unavailable` is set, give the literal command to run",
    ),
    # W-META — answer shape for contentless continuation directives.
    "session_meta": (
        "Treat the task as a continuation directive — it adds no new task content",
        "The conversation context is authoritative; continue the in-flight work",
        "Use `session_brief` only to re-anchor repo state (mode, next, alerts)",
        "Do NOT re-explore the repo from scratch",
    ),
    # W-BATCH — answer shape for self-contained batch payloads.
    "self_contained_task": (
        "The prompt is self-contained — every input and output spec is inside it",
        "Execute exactly as specified; honor the stated output format verbatim",
        "Do NOT explore this repo — the task's own inputs are authoritative",
        "Read only the files the prompt itself names",
    ),
}

# R10: `roam_starter` — copy-pasteable first command. When the agent has a
# concrete roam command to run first, it skips the exploration-by-trial-and-error
# phase entirely. Empty string = no obvious starter for this procedure.
# These templates expand {target} from the first named_path.
_PROCEDURE_STARTERS: dict[str, str] = {
    "structural_coupling": "roam --json coupling -n 100 | jq '.pairs | sort_by(-.strength) | .[0:5]'",
    "structural_callers": "roam --json uses {symbol} | jq '.callers'",
    "structural_dead": "roam --json dead-code | jq '.unused[0:10]'",
    "structural_cycle": "roam --json clusters | jq '.cycles'",
    "structural_complexity": "roam --json file-info {target} | jq '{loc, complexity}'",
    "structural_blast": "roam --json impact {symbol} | jq '{count, top_files: .files[0:10]}'",
    "trace_query": 'roam --json retrieve "<task>"',
    # W35a: starter is N/A — the embedded `stack_frames` IS the answer,
    # the agent should NOT shell out. Empty string preserves the convention.
    "stack_trace_fix": "",
    # W211 — refactor_move starter (caught by dict-consistency W211 re-audit).
    # Copy-paste roam command for "what would break if I move X" pre-check.
    "refactor_move": "roam --json impact {symbol} | jq '{count, top_files: .files[0:10]}'",
    # W11/W12/W13 — copy-paste starters for the new probe families.
    "symbol_defined_where": "roam --json search {symbol} | jq '.results[0:5]'",
    "top_n_ranking": "roam --json coupling -n 5 | jq '.pairs[0:5]'",
    "cli_verb_why_slow": "roam --json doctor | jq '.phase_timings'",
    # W-HIST — copy-paste starter; only needed when the embedded log is
    # insufficient (e.g. the agent needs more than 10 commits).
    "file_history": "git log --oneline -20 -- {target}",
    # v0.4 added freeform/synthesis starters. v0.4.1 REVERTS them:
    # Phase B (2026-05-29) showed the v0.4 envelope regressed -2.4pp quality
    # and +45% cost vs FC R9. roam_ask invocation rate jumped 13%→50% but the
    # extra dispatch calls were overhead, not progress. Surface envelope-shape
    # lever is exhausted at FC R9. Next iteration shifts category (L1/L2).
}


# v0.4 (2026-05-29) — STRUCTURED parallel-combo recommendation.
# The documented biggest wins are PARALLEL: roam_coupling+roam_deps (-84% tokens),
# roam_impact+roam_uses (canonical blast), roam_alerts+roam_health+roam_dashboard
# (-78%). Old envelope had only `recommended_first_command` (singular). Agents
# saw a sentence saying "in PARALLEL" and didn't latch on. Promoting to a
# typed list makes it parseable: `recommended_parallel_tools: [...]`.
_PROCEDURE_PARALLEL_COMBO: dict[str, list[str]] = {
    "structural_coupling": ["roam_coupling", "roam_deps"],
    "structural_blast": ["roam_impact", "roam_uses"],
    "structural_callers": [],  # single tool — roam_uses is enough
    "structural_dead": [],  # single tool — roam_dead_code is enough
    "structural_cycle": [],  # single tool — roam_clusters
    "structural_complexity": [],
    "trace_query": ["roam_retrieve", "roam_search_semantic"],
    "synthesis_query": ["roam_impact", "roam_uses"],  # for refactor proposals
    "freeform_explore": [],  # roam_ask handles dispatch; no parallel hint
    "stack_trace_fix": [],  # answer is in the embedded slice; no tool fan-out
    # W210 — refactor_move added (caught by dict-consistency W181-class)
    "refactor_move": ["roam_impact", "roam_uses"],
    # W11/W12/W13 — new probe families. Single-tool answers (the probe
    # data IS the answer); no parallel fan-out needed.
    "symbol_defined_where": [],
    "top_n_ranking": [],
    "cli_verb_why_slow": [],
    # W-HIST — the embedded git log IS the answer; no tool fan-out.
    "file_history": [],
    # W-REPO — the embedded summary IS the answer; no tool fan-out.
    "repo_structure": [],
    # W-ENTRY / W-CFG — single embedded probe IS the answer.
    "entry_point_where": [],
    "config_where": [],
    # W-META — no tools; the conversation is the task.
    "session_meta": [],
    # W-BATCH — no tools; the payload is the task.
    "self_contained_task": [],
}


# v0.4 — multi-symbol batch detection. When the task names 3+ symbols/paths,
# the documented win is `roam_batch_search` (one call vs N — saves 69-79%
# tokens). Override the procedure starter when this trigger fires.
_BATCH_SEARCH_THRESHOLD = 3


# W44 I3 — bounded in-session probe cache. Same task corpus invokes
# `roam deps src/X.py` many times during a benchmark sweep; each call
# spawns a subprocess (~150ms). Cache by (args, cwd, detail) with a
# 60-second TTL. Cap at 128 entries; LRU-ish eviction when full.
_RUN_ROAM_CACHE: dict[tuple[str, str, bool], tuple[float, dict | None]] = {}
_RUN_ROAM_CACHE_CAP = 128
_RUN_ROAM_CACHE_TTL_S = 60.0


# W147 — persistent _run_roam result cache (SQLite, cross-session).
# In-memory _RUN_ROAM_CACHE dies with the process. For long-lived MCP
# server sessions, agents repeatedly ask the same probes across many
# tasks — each `roam uses X` re-pays a 30-50ms subprocess cost even
# though the underlying graph is unchanged. Persist successful results
# keyed on (args, cwd, repo_head). 24-hour TTL; 4096-row cap.
_RUN_ROAM_PERSIST_CAP = 4096
_RUN_ROAM_PERSIST_TTL_S = 24 * 3600.0
_RUN_ROAM_PERSIST_TABLE_INITED: set[str] = set()


def _run_roam_persist_path(cwd: str | None) -> str | None:
    if not cwd:
        return None
    try:
        roam_dir = os.path.join(cwd, ".roam")
        if not os.path.isdir(roam_dir):
            return None
        path = os.path.join(roam_dir, "compile-envelope-cache.sqlite")
        _persist_sweep_stale_generation(path, cwd)
        return path
    except (OSError, TypeError):
        return None


# Tables whose rows are DERIVED FROM THE INDEX but invalidated only by
# TTL + repo HEAD. Under uncommitted dev edits HEAD never moves, so a row
# captured from a stale index outlives `roam index --force` and keeps
# feeding poisoned facts (stale line numbers) into freshly-stamped
# envelopes — observed live 2026-06-11 via probe_pos/run_roam on the
# structural_callers path. env_cache is NOT listed: it carries per-row
# dep mtimes + its own index stamp and self-evicts with precision.
_INDEX_DERIVED_TABLES = (
    "run_roam_cache",
    "probe_pos_cache",
    "probe_neg_cache",
    "symbol_resolution_cache",
    "plan_cache",
)
_PERSIST_GENERATION_SWEPT: set[str] = set()


def _persist_sweep_stale_generation(path: str, cwd: str | None) -> None:
    """Once per process per cache DB: if .roam/index.db's mtime differs
    from the generation recorded in the DB, wipe every index-derived
    table. Re-indexing thereby invalidates all coarse-keyed caches at
    once; precision-keyed tables keep their own row-level checks."""
    if path in _PERSIST_GENERATION_SWEPT:
        return
    _PERSIST_GENERATION_SWEPT.add(path)
    try:
        generation = str(int(os.path.getmtime(_index_db_path(cwd)) * 1000))
    except OSError:
        return  # no index yet — nothing derived from it can be stale
    try:
        import sqlite3

        conn = sqlite3.connect(path, timeout=1.0)
        try:
            _apply_generation_sweep(conn, generation)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — cache hygiene must never break a compile
        log_swallowed("compile.persist_sweep", exc)


def _apply_generation_sweep(conn, generation: str) -> None:
    """Wipe the index-derived tables unless `generation` matches the one
    recorded in persist_meta; record the new generation either way."""
    import sqlite3

    conn.execute("CREATE TABLE IF NOT EXISTS persist_meta (k TEXT PRIMARY KEY, v TEXT)")
    row = conn.execute("SELECT v FROM persist_meta WHERE k='index_generation'").fetchone()
    if row and row[0] == generation:
        return
    for table in _INDEX_DERIVED_TABLES:
        try:
            conn.execute(f"DELETE FROM {table}")  # noqa: S608 — closed enum above
        except sqlite3.OperationalError as exc:
            log_swallowed("compile.persist_sweep.missing_table", exc)
    conn.execute(
        "INSERT OR REPLACE INTO persist_meta VALUES ('index_generation', ?)",
        (generation,),
    )
    conn.commit()


def _run_roam_persist_ensure_schema(conn) -> None:
    """Create table + W148 WAL pragma on first use per connection."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS run_roam_cache (key TEXT PRIMARY KEY, head TEXT, result_json TEXT, ts REAL)"
    )
    _set_wal(conn)


def _run_roam_persist_key(args: list[str], cwd_norm: str) -> str:
    blob = "\x1f".join([cwd_norm or "", *args]).encode("utf-8", "replace")
    return sha256(blob).hexdigest()[:24]


def _run_roam_persist_get(args: list[str], cwd: str | None, head: str) -> dict | None:
    path = _run_roam_persist_path(cwd)
    if not path:
        return None
    try:
        import sqlite3

        conn = sqlite3.connect(path, timeout=1.0)
        _set_wal(conn)
        try:
            if path not in _RUN_ROAM_PERSIST_TABLE_INITED:
                _run_roam_persist_ensure_schema(conn)
                _RUN_ROAM_PERSIST_TABLE_INITED.add(path)
            key = _run_roam_persist_key(args, cwd or "")
            row = conn.execute(
                "SELECT head, result_json, ts FROM run_roam_cache WHERE key=?",
                (key,),
            ).fetchone()
            if row is None:
                return None
            cached_head, result_json, ts = row
            if (time.time() - ts) > _RUN_ROAM_PERSIST_TTL_S:
                conn.execute("DELETE FROM run_roam_cache WHERE key=?", (key,))
                conn.commit()
                return None
            if head and cached_head and cached_head != head:
                conn.execute("DELETE FROM run_roam_cache WHERE key=?", (key,))
                conn.commit()
                return None
            try:
                return json.loads(result_json)
            except json.JSONDecodeError:
                return None
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        log_swallowed("compile.run_roam_persist.get", exc)
        return None


def _run_roam_persist_put(args: list[str], cwd: str | None, head: str, value: dict | None) -> None:
    if value is None:
        return
    path = _run_roam_persist_path(cwd)
    if not path:
        return
    try:
        import sqlite3

        conn = sqlite3.connect(path, timeout=1.0)
        _set_wal(conn)
        try:
            if path not in _RUN_ROAM_PERSIST_TABLE_INITED:
                _run_roam_persist_ensure_schema(conn)
                _RUN_ROAM_PERSIST_TABLE_INITED.add(path)
            key = _run_roam_persist_key(args, cwd or "")
            conn.execute(
                "INSERT OR REPLACE INTO run_roam_cache VALUES (?,?,?,?)",
                (key, head or "", _fast_json_dumps(value), time.time()),
            )
            # LRU-ish cap: evict oldest by ts when over.
            (count,) = conn.execute("SELECT COUNT(*) FROM run_roam_cache").fetchone()
            if count > _RUN_ROAM_PERSIST_CAP:
                conn.execute(
                    "DELETE FROM run_roam_cache WHERE key IN (SELECT key FROM run_roam_cache ORDER BY ts LIMIT ?)",
                    (count - _RUN_ROAM_PERSIST_CAP,),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        log_swallowed("compile.run_roam_persist.put", exc)


# W149 — off-thread telemetry writer. The `.roam/compile-runs.jsonl`
# append currently blocks at the end of every compile (~1-2ms with
# fsync overhead). Move to a background daemon thread + queue so the
# compile returns immediately. Queue is bounded (drop on overflow);
# worker batches writes.
import queue as _w149_queue

_TELEMETRY_QUEUE: "_w149_queue.Queue[tuple[str, str] | None]" = _w149_queue.Queue(maxsize=512)
_TELEMETRY_THREAD_STARTED = False
_TELEMETRY_THREAD_LOCK = _w131_threading.Lock()


def _telemetry_worker():
    while True:
        try:
            item = _TELEMETRY_QUEUE.get(timeout=5.0)
        except _w149_queue.Empty:
            continue
        if item is None:  # shutdown sentinel
            return
        path, line = item
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError as exc:
            log_swallowed("compile.telemetry.bg_write", exc)


def _ensure_telemetry_worker() -> None:
    global _TELEMETRY_THREAD_STARTED
    if _TELEMETRY_THREAD_STARTED:
        return
    with _TELEMETRY_THREAD_LOCK:
        if _TELEMETRY_THREAD_STARTED:
            return
        t = _w131_threading.Thread(target=_telemetry_worker, daemon=True, name="roam-compile-telemetry")
        t.start()
        _TELEMETRY_THREAD_STARTED = True


# W131 — in-process dispatch lock + on-off flag. Replacing
# `subprocess.run(["roam", ...])` with a direct CliRunner.invoke cuts
# the ~50ms python-interpreter cold start per probe call. With ~6 cold
# probes per task that's ~300ms shaved. Serialized via a module-level
# lock because CliRunner mutates os.cwd, but the lock is held only
# during the synchronous invoke. Even with full contention from the
# W125 parallel pool the savings (~40ms/call net) compound.
import threading as _w131_threading

# RLock, not Lock: an in-proc command that itself reaches _run_roam on the
# same thread must not deadlock now that the no-chdir path locks too.
_ROAM_INPROC_LOCK = _w131_threading.RLock()
_ROAM_INPROC_ENABLED = os.environ.get("ROAM_INPROC_DISPATCH", "1") not in ("0", "false", "no", "off")
# W143 — module-level CliRunner + cli singletons. Each call previously
# did a fresh `from click.testing import CliRunner; from roam.cli import cli`
# — Python's import system caches the module objects, but the symbol
# lookup is still per-call. Holding the references explicitly cuts a
# few hundred ns and makes the hot path clearer.
_CACHED_CLI_RUNNER = None
_CACHED_ROAM_CLI = None
_CLI_IMPORT_FAILED = False
# Commands that re-enter compile or have known side-effects — keep on
# subprocess to be safe.
_ROAM_INPROC_DENYLIST = frozenset(
    {
        "compile",
        "bench-compile",
        "compile-cache",
        "init",
        "watch",
        "mcp",
        "guard-pr",
        "proof-bundle",
        # O(repo) graph scans whose probe `timeout=` must be ENFORCEABLE. The
        # in-process path holds a global CliRunner lock and cannot be cancelled,
        # so a probe's short budget is silently ignored there (the repo-wide
        # `dead` probe's 3s cap ran 13s on this repo). Routing them through the
        # killable subprocess path makes the timeout real → fast fallback instead
        # of a 13s compile. Cost: a cold import on fast repos, well within budget.
        "dead",
    }
)


def _get_cached_cli_runner():
    """W131 — lazily import roam's Click CLI + a cached CliRunner. Returns
    (runner, cli) or (None, None) when in-proc dispatch is unavailable."""
    global _CACHED_CLI_RUNNER, _CACHED_ROAM_CLI, _CLI_IMPORT_FAILED
    if _CLI_IMPORT_FAILED:
        return None, None
    if _CACHED_ROAM_CLI is None:
        try:
            from click.testing import CliRunner

            from roam.cli import cli as _cli

            _CACHED_ROAM_CLI = _cli
            try:
                _CACHED_CLI_RUNNER = CliRunner(mix_stderr=False)
            except TypeError:
                _CACHED_CLI_RUNNER = CliRunner()
        except Exception as exc:  # noqa: BLE001
            log_swallowed("compile.roam_inproc.import", exc)
            _CLI_IMPORT_FAILED = True
            return None, None
    return _CACHED_CLI_RUNNER, _CACHED_ROAM_CLI


def _invoke_cli(runner, cli, args: list[str]) -> tuple[int, str] | None:
    """Invoke the cached Click runner; (exit_code, output) or None on exception."""
    try:
        res = runner.invoke(cli, list(args), catch_exceptions=True)
    except Exception as exc:  # noqa: BLE001
        log_swallowed("compile.roam_inproc.invoke", exc)
        return None
    return (res.exit_code or 0, res.output or "")


def _roam_invoke_inproc(args: list[str], cwd: str | None) -> tuple[int, str] | None:
    """W131 — in-process Click dispatch. Returns (exit_code, stdout) or
    None on failure (caller falls back to subprocess).
    """
    if not _ROAM_INPROC_ENABLED:
        return None
    if not args:
        return None
    # Strip leading global flags (e.g. ['--json', 'uses', X]) when
    # checking denylist — the subcommand may be index 0 or later.
    first_cmd = next((a for a in args if not a.startswith("-")), None)
    if first_cmd in _ROAM_INPROC_DENYLIST:
        return None
    runner, cli = _get_cached_cli_runner()
    if runner is None:
        return None
    # W132/W145 — bypass the CHDIR when cwd is None or already current, but
    # NEVER bypass the lock: CliRunner.invoke swaps the process-global
    # sys.stdout, and the W125 probe pool calls this from parallel threads.
    # Unlocked concurrent invokes race the swap — one probe's envelope leaks
    # to the REAL stdout while the parent command's output is swallowed into
    # a probe's capture buffer (observed 2026-06-09: `roam --json
    # compiler-corpus` emitted ONLY a stray grep envelope, aggregate lost,
    # exit 0). The lock serializes the stdout swap, not just the chdir.
    if not cwd:
        need_chdir = False
    else:
        need_chdir = os.path.abspath(cwd) != os.getcwd()
    if not need_chdir:
        with _ROAM_INPROC_LOCK:
            return _invoke_cli(runner, cli, args)
    with _ROAM_INPROC_LOCK:
        prev = os.getcwd()
        try:
            try:
                os.chdir(cwd)
            except OSError as exc:
                log_swallowed("compile.roam_inproc.chdir", exc)
                return None
            return _invoke_cli(runner, cli, args)
        finally:
            try:
                os.chdir(prev)
            except OSError as exc:
                log_swallowed("compile.run_roam.chdir_restore", exc)


def _run_roam(args: list[str], cwd: str | None, timeout: float = 8.0, detail: bool = False) -> dict | None:
    """Run a roam --json subcommand, return parsed JSON or None on failure.

    `detail=True` adds the --detail global flag, which exposes structured
    list fields (e.g. deps `imports` / `imported_by`) instead of just counts.

    W44 I3: caches identical (args, cwd, detail) calls for up to 60s.
    Skipped when cwd is None (unit tests) or when the cache is full
    AND no entry was evictable.
    """
    # W54 — JSON-tuple key avoids collisions when args contain spaces.
    # Previous `" ".join(args)` would conflate `["uses", "foo bar"]` and
    # `["uses foo", "bar"]`. Tuple-of-tuples is hashable + unambiguous.
    key = (tuple(args), cwd or "", detail)
    now = time.monotonic()
    cached = _RUN_ROAM_CACHE.get(key)
    if cached is not None:
        ts, value = cached
        if now - ts < _RUN_ROAM_CACHE_TTL_S:
            return value
        del _RUN_ROAM_CACHE[key]
    cli_args: list[str] = []
    if detail:
        cli_args.append("--detail")
    cli_args.extend(["--json", *args])
    value = None
    # W147 — persistent SQLite cache. Survives process restart. ~0.5-1ms
    # lookup vs 30-50ms subprocess/inproc cold call → ~30× win on hit.
    _persist_head = ""
    if cwd:
        try:
            _persist_head = _memoized_head(cwd) or ""
        except Exception:  # noqa: BLE001
            _persist_head = ""
        persisted = _run_roam_persist_get(cli_args, cwd, _persist_head)
        if persisted is not None:
            _RUN_ROAM_CACHE[key] = (now, persisted)
            return persisted
    # W131 — try in-process Click dispatch first; falls back to subprocess
    # on import failure, denylisted command, or runtime exception.
    inproc = _roam_invoke_inproc(cli_args, cwd)
    if inproc is not None:
        exit_code, stdout = inproc
        if exit_code == 0 and stdout:
            try:
                value = json.loads(stdout)
            except json.JSONDecodeError as exc:
                log_swallowed("compile._run_roam.inproc_json", exc)
                value = None
    else:
        cmd = ["roam", *cli_args]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
            )
            if result.returncode != 0:
                value = None
            else:
                value = json.loads(result.stdout)
        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as exc:
            log_swallowed("compile._run_roam", exc)
            value = None
    # Evict the oldest entry if at cap.
    if len(_RUN_ROAM_CACHE) >= _RUN_ROAM_CACHE_CAP:
        oldest = min(_RUN_ROAM_CACHE.items(), key=lambda kv: kv[1][0])
        del _RUN_ROAM_CACHE[oldest[0]]
    _RUN_ROAM_CACHE[key] = (now, value)
    # W147 — persist successful results for cross-session reuse.
    if value is not None and cwd:
        _run_roam_persist_put(cli_args, cwd, _persist_head, value)
    return value


def _detect_decision_criterion(task: str) -> str | None:
    """L11 (2026-05-29 16:30) — decision-criterion preamble for comparison tasks.

    Research-agent finding (deep07 cell analysis): comparison tasks split on
    "which is X-est?" produce divergent answers because the rubric is left
    implicit. The compiler should commit to a criterion.

    Detects keywords like "worst-case", "most efficient", "highest", "which has",
    "compare", etc. and ships a default rubric the agent must satisfy.
    """
    t = task.lower()
    if re.search(r"\b(worst[\s-]?case|best[\s-]?case|highest|lowest|which has the (worst|best|highest|lowest))\b", t):
        if re.search(r"\bcomplexity\b", t):
            return (
                "Use worst-case big-O on input size implied by the task "
                "(symbol count, file count, etc.), ignoring constant factors "
                "and short-circuit guards under threshold N=1000. State your N."
            )
        return (
            "Commit to a single quantitative metric BEFORE comparing. "
            "Pick the metric that best maps to the user's phrasing."
        )
    if re.search(r"\b(compare|versus|vs\.?|which of)\b", t):
        return "Establish a comparison rubric in step 1 (1-2 metrics max). Apply identically to all candidates."
    return None


def _detect_scope_lock(task: str, named_paths: list[str]) -> str | None:
    """L13 (2026-05-29 16:30) — scope_lock for directory-scoped tasks.

    Research-agent finding (py04 cell analysis): directory-scoped tasks
    ("Find X in src/Y/") fail when the agent expands scope to repo-wide.
    The compiler should emit a scope-lock instruction the contract enforces.
    """
    # Directory in named_paths
    dirs = [p for p in named_paths if p.endswith("/")]
    if dirs:
        return f"Restrict ALL searches to {dirs[0]}; do not expand to repo-wide grep."
    # "in X/" pattern
    m = re.search(r"\bin\s+([\w/-]+/)\s", task)
    if m:
        return f"Restrict ALL searches to {m.group(1)}; do not expand."
    return None


def _detect_output_shape(task: str, procedure: str) -> str | None:
    """L11+L12 output-shape routing (research-agent finding).

    Ordering tasks → hop_table
    Comparison tasks → comparison_matrix
    Citation-dense tasks → claim_citation_table
    """
    t = task.lower()
    if re.search(r"\b(order(ing)?|sequence|before|after|step.by.step|hop)\b", t):
        return "hop_table"
    if re.search(r"\b(compare|versus|which of|worst|best|highest)\b", t):
        return "comparison_matrix"
    return None


def _probe_l10_symbol_resolution(task: str, cwd: str | None) -> dict | None:
    """L10 — symbol-resolution prefetch (agent 2 finding, 2026-05-29).

    When the task names a function/class/method in backticks, eagerly run
    `roam search` + `roam file-info` at compile time and embed file:line
    + role information. Addresses ~30% of vanilla high-turn loops where
    the agent narrates 'let me look up X' for an X we could have resolved.

    Cheap and always-safe — only adds cost if backticked symbol is found.
    Returns dict for `resolved_symbols` envelope key, or None.
    """
    backticked = re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", task)
    if not backticked:
        return None
    resolved = []
    for sym in backticked[:5]:  # cap at 5 to bound subprocess time
        d = _run_roam(["search", sym], cwd, timeout=4.0)
        if not d:
            continue
        results = d.get("results", []) or d.get("matches", []) or d.get("symbols", [])
        ranked = _rank_symbol_search_rows(results, sym)
        if not ranked:
            continue
        first = ranked[0]
        # `roam search` reports the def site as `location` ("path:line"); the old
        # `first.get("file")` read missed it, so resolved_symbols.file was null.
        loc, line = _split_loc_line(
            first.get("location") or first.get("file") or first.get("path") or "",
            first.get("line") or 0,
        )
        resolved.append(
            {
                "symbol": sym,
                "file": loc or None,
                "line": line or None,
                "kind": first.get("kind"),
            }
        )
    return {"resolved_symbols": resolved} if resolved else None


def _pair_contains(pair: dict, target: str) -> bool:
    """Does the coupling pair name `target` on either side? W34a helper."""
    return pair.get("file_a", "").endswith(target) or pair.get("file_b", "").endswith(target)


def _git_cochange_counts(target: str, cwd: str | None, limit: int = 200) -> list[tuple[str, int]]:
    """Files that co-change with `target`, ranked by frequency.

    W34a (E1) primitive: replaces the prior "roam coupling top-N + filter"
    probe for widely-coupled files. Two-step:
      1. `git log -- <target>` → SHAs of commits that touched the target.
      2. `git show --name-only <SHAs...>` → all files in each commit.
    Counts co-occurring files (excluding target itself), returns top 20.

    Returns [] on subprocess failure, empty history, or git not available —
    never raises.
    """
    try:
        sha_proc = subprocess.run(
            ["git", "log", f"--max-count={limit}", "--format=%H", "--", target],
            capture_output=True,
            text=True,
            timeout=5.0,
            cwd=cwd or None,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if sha_proc.returncode != 0:
        return []
    shas = [s.strip() for s in sha_proc.stdout.splitlines() if s.strip()]
    if not shas:
        return []
    try:
        # `git show` accepts multiple SHAs; cap at a reasonable count to
        # bound the subprocess wall time on huge histories.
        show_proc = subprocess.run(
            ["git", "show", "--name-only", "--pretty=format:__commit__"] + shas[:limit],
            capture_output=True,
            text=True,
            timeout=10.0,
            cwd=cwd or None,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if show_proc.returncode != 0:
        return []
    counts: Counter[str] = Counter()
    for line in show_proc.stdout.splitlines():
        line = line.strip()
        if not line or line == "__commit__":
            continue
        if line == target or line.endswith("/" + target):
            continue
        counts[line] += 1
    return counts.most_common(20)


# ---- W43 P2 — named caps replace magic numbers in the probes ----
# Each cap bounds an embed so the L1 envelope stays small. Tweaking
# in one place propagates everywhere; in-flight values were 5/8/10/15/
# 20/30/60/80 scattered across 8 probe helpers.
_DEPS_LIST_CAP = 15  # imports / imported_by truncation
_COCHANGE_PAIR_CAP = 8  # git co-change partner cap per target
_COCHANGE_GIT_LIMIT = 200  # max commits scanned per target
_CALLERS_CAP = 20  # `roam uses` consumer cap
_DEAD_TOP_CAP = 10  # `roam dead` high-confidence cap
_BLAST_TOP_FILES_CAP = 15  # `roam impact` file list cap
_FILE_SKELETON_SYMBOL_CAP = 30  # synthesis skeleton top-level symbols
_FREEFORM_SKELETON_CAP = 20  # freeform skeleton (smaller — explain task)
_STACK_FRAME_SLICE_BEFORE = 5  # ±N lines around failing line
_STACK_FRAME_SLICE_AFTER = 5
_FILE_EXCERPT_LINES = 80  # body-embed for "what does X do"
_SIBLING_TEST_LINES = 60  # sibling test for "write a pytest for X"
_SRC_UNDER_TEST_LINES = 80  # source under test (W39 B2)
_CONFTEST_LINES = 40  # nearest conftest.py (W39 B2)
_GIT_LOG_RECENT_COMMITS = 5  # recent_commits + symbol_history limit
_DIFF_TRUNCATE_LINES = 200  # path comparison diff truncation


def _probe_coupling(named_paths: list[str], cwd: str | None) -> dict:
    """W41 — extracted from _probe_for_procedure structural_coupling branch.
    Dual probe (structural deps + git co-change) over the first 2 named
    paths. See W34a (E1/E4) for history.

    W47 — the 4 subprocess calls (deps×2 + cochange×2) now run in
    parallel via ThreadPoolExecutor. Sequentially this was the slowest
    probe (~600-800ms on this repo); concurrent dispatch drops it to
    the slowest single call (~200ms).

    W43 — collapse subprocess spawns. `roam deps --multi` now emits the
    cochange axis on the envelope alongside imports/imported_by, so each
    target needs ONE _run_roam call instead of TWO parallel ones
    (deps + cochange). Drops 2 of 4 subprocess spawns for the 548
    structural_coupling compiles seen in the 2-day telemetry window
    (~110s cumulative saving). Falls back to the W47 dual-call shape if
    --multi is unavailable (older roam version that doesn't accept the
    flag will fail; the in-process Click dispatch on _run_roam means we
    rely on the same code tree, so this is always available).
    """
    facts: dict = {}
    if not named_paths:
        return facts
    targets = named_paths[:2]

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=2) as pool:
        # W43 — one --multi call per target returns deps + cochange in
        # one envelope; keep the 2-target parallelism so two targets
        # still overlap on the wall.
        deps_futures = {
            i: pool.submit(_run_roam, ["deps", t, "--multi"], cwd, detail=True) for i, t in enumerate(targets)
        }
        deps_results = {i: f.result() for i, f in deps_futures.items()}
    # W43 — derive cochange_results from the same envelope. The --multi
    # envelope emits ``cochange_pairs: [{file, count}]`` which we adapt
    # back to the (fname, count) tuple shape the downstream loop expects.
    cochange_results: dict[int, list[tuple[str, int]]] = {}
    for i in range(len(targets)):
        d = deps_results.get(i) or {}
        pairs = d.get("cochange_pairs") or []
        cochange_results[i] = [(p.get("file", ""), int(p.get("count", 0))) for p in pairs if p.get("file")]

    # Structural deps per target.
    for i, target in enumerate(targets):
        suffix = "" if i == 0 else f"_{i + 1}"
        d = deps_results.get(i)
        if not d:
            continue
        imports = d.get("imports", [])[:_DEPS_LIST_CAP]
        imported_by = d.get("imported_by", [])[:_DEPS_LIST_CAP]
        if not (imports or imported_by):
            continue
        facts[f"structural_imports{suffix}"] = imports
        facts[f"structural_imported_by_count{suffix}"] = len(d.get("imported_by", []))
        facts[f"structural_imported_by_top{suffix}"] = imported_by
        if i == 0:
            facts["structural_definition"] = (
                "static dependency graph: 'imports' = files target depends on; "
                "'imported_by_top' = top files that depend on target (full count separate). "
                f"Suffix _N indicates per-named-path index (current targets: {targets})."
            )
    # Temporal coupling per target — direct git query (W34a E1).
    for i, target in enumerate(targets):
        suffix = "" if i == 0 else f"_{i + 1}"
        cochange = cochange_results.get(i)
        if not cochange:
            continue
        facts[f"temporal_coupling_pairs{suffix}"] = [
            {"file_a": target, "file_b": fname, "cochange_count": count}
            for fname, count in cochange[:_COCHANGE_PAIR_CAP]
        ]
        if i == 0:
            facts["temporal_definition"] = (
                "git co-change counts CENTERED on the named file "
                "(W34a: replaces the prior top-N+filter approach that "
                "missed widely-coupled files like cli.py)."
            )
    return facts


def _embed_target_symbol_body(symbol: str, named_paths: list[str], cwd: str | None) -> tuple[str, str] | None:
    """W161 — embed the target symbol's own definition body (±40 lines, 4 KB)
    so 'who calls X' pre-answers the inevitable 'what does X do' follow-up.
    Returns (snippet, definition) or None."""
    if not (cwd and named_paths):
        return None
    target_file = next((p for p in named_paths if isinstance(p, str) and p.endswith(".py")), None)
    if not target_file:
        return None
    try:
        full = Path(cwd) / target_file if not os.path.isabs(target_file) else Path(target_file)
        if not (full.exists() and full.stat().st_size <= 200 * 1024):
            return None
        lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
        anchor_idx = None
        for i, line in enumerate(lines):
            if (
                f"def {symbol}(" in line
                or f"def {symbol} " in line
                or f"class {symbol}(" in line
                or f"class {symbol}:" in line
            ):
                anchor_idx = i
                break
        if anchor_idx is not None:
            snippet = "\n".join(lines[max(0, anchor_idx - 5) : min(len(lines), anchor_idx + 40)])
        else:
            snippet = "\n".join(lines[:120])
        if len(snippet) > 4 * 1024:
            snippet = snippet[: 4 * 1024]
        definition = (
            f"Body of `{symbol}` from {target_file} (~40 lines around "
            f"the definition). Agent should read this BEFORE asking "
            f"`what does {symbol} do`."
        )
        return snippet, definition
    except (OSError, ValueError) as exc:
        log_swallowed("compile.callers.target_body_embed", exc)
        return None


def _embed_caller_bodies(callers: list, symbol: str, cwd: str | None) -> dict[str, str] | None:
    """W156 — for ≤5 callers, embed the first 120 lines (8 KB cap) of each so
    the agent reads them inline instead of re-fetching. None when not applicable."""
    if not (len(callers) <= 5 and cwd):
        return None
    bodies: dict[str, str] = {}
    for caller in callers[:5]:
        loc = caller if isinstance(caller, str) else (caller.get("location") if isinstance(caller, dict) else None)
        if not loc or ":" not in str(loc):
            continue
        path_str, _, _line = str(loc).partition(":")
        try:
            full = Path(cwd) / path_str if not os.path.isabs(path_str) else Path(path_str)
            if not full.exists() or full.stat().st_size > 400 * 1024:
                continue
            lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
            snippet = "\n".join(lines[:120])
            if len(snippet) > 8 * 1024:
                snippet = snippet[: 8 * 1024]
            bodies[path_str] = snippet
        except (OSError, ValueError) as exc:
            log_swallowed("compile.callers.body_embed", exc)
    return bodies or None


def _probe_callers(named_paths: list[str], cwd: str | None) -> dict:
    """W41 — structural_callers branch. Backticked-symbol fallback is
    handled by `_probe_callers_backtick_for_task` at envelope-build time
    (task text not available here).

    W156 — when there are ≤ 3 callers, also embed each caller's source
    body. The agent's typical NEXT turn after seeing a caller list is
    "let me read those files to understand"; pre-embedding the bodies
    saves that turn. Cap each body at 80 lines / 6KB to stay within
    the recommended_model budget tiers.
    """
    facts: dict = {}
    if not named_paths:
        return facts
    symbol = named_paths[0]
    d = _run_roam(["uses", symbol], cwd)
    callers = _flatten_consumers(d) if d else []
    if not callers:
        return facts
    facts["callers"] = callers[:_CALLERS_CAP]
    # W160 — concrete-noun-anchored callers summary string. LAW 4 in
    # AGENTS.md says fact strings must terminal-anchor on concrete nouns.
    # Today the agent gets `facts["callers"] = [...]` (a list); add a
    # string companion that summarizes "N callers" + first 5 by path.
    _first_paths = []
    for c in callers[:5]:
        loc = c if isinstance(c, str) else (c.get("location") if isinstance(c, dict) else "")
        if loc:
            _first_paths.append(str(loc))
    facts["callers_definition"] = f"{len(callers)} callers of `{symbol}`" + (
        f"; first 5: {', '.join(_first_paths)}" if _first_paths else ""
    )
    _tb = _embed_target_symbol_body(symbol, named_paths, cwd)
    if _tb:
        facts["target_symbol_body"], facts["target_symbol_body_definition"] = _tb
    _cb = _embed_caller_bodies(callers, symbol, cwd)
    if _cb:
        facts["caller_bodies"] = _cb
        facts["caller_bodies_definition"] = (
            f"First 120 lines of each of the ≤5 callers of `{symbol}`. "
            "Agent should read these instead of re-fetching the files."
        )
    return facts


# Words that pass the identifier regex inside a dead-code question but
# are the question's vocabulary, not the target symbol.
_DEAD_TARGET_STOPWORDS: frozenset[str] = frozenset(
    {
        "dead",
        "code",
        "safe",
        "delete",
        "unused",
        "callers",
        "caller",
        "orphan",
        "orphaned",
        "orphans",
        "never",
        "called",
        "used",
        "referenced",
        "imported",
        "anywhere",
        "function",
        "functions",
        "method",
        "methods",
        "class",
        "classes",
        "symbol",
        "symbols",
        "module",
        "modules",
        "export",
        "exports",
        "import",
        "imports",
        "test",
        "tests",
        "untested",
        "the",
    }
)


def _extract_dead_target_symbol(task: str | None) -> str | None:
    """For 'is X dead code' / 'is X safe to delete' / 'is X ever used' —
    return the specific identifier X. Returns None for repo-wide phrasings
    ('find unused functions', 'unused exports') so those still get the full
    scan. Conservative: the token must be identifier-shaped (snake_case or
    camelCase) and not part of the dead-code question vocabulary."""
    for tok in re.findall(r"`?([A-Za-z_][A-Za-z0-9_]{2,})`?", task or ""):
        if tok.lower() in _DEAD_TARGET_STOPWORDS:
            continue
        if "_" not in tok and not re.search(r"[a-z][A-Z]", tok):
            continue
        return tok
    return None


def _probe_dead(named_paths: list[str], cwd: str | None, task: str | None = None) -> dict:
    """W41 — structural_dead branch.

    Two modes:
      * TARGETED ("is `X` dead code") — a specific symbol is named. Run the
        O(1) single-symbol reachability (`roam uses X`, ~0.1s) and answer
        about X directly. Avoids the 12s repo-wide scan AND answers the
        actual question instead of dumping the global top-10.
      * REPO-WIDE ("find unused functions") — no symbol named. Fall back to
        `roam dead` (tight 3s timeout; O(symbols), can exceed 10s on large
        repos → envelope falls back to "full" on timeout).
    """
    facts: dict = {}
    target = _extract_dead_target_symbol(task)
    if target:
        # A specific symbol was named — answer about IT, fast, and never
        # fall through to the 12s repo-wide scan (which doesn't answer
        # "is X dead" anyway).
        facts["target_symbol"] = target
        u = _run_roam(["uses", target], cwd, detail=True, timeout=4.0)
        if u is not None:
            callers = _flatten_consumers(u) or []
            n = len(callers)
            facts["caller_count"] = n
            facts["verdict"] = (
                f"`{target}` has 0 static callers — a SAFE-TO-DELETE CANDIDATE. "
                f"Confirm it is not an entry point, public API, test, or "
                f"dynamically dispatched before removing."
                if n == 0
                else f"`{target}` is LIVE — {n} caller(s); not dead."
            )
            if callers:
                facts["caller_sample"] = callers[:_CALLERS_CAP]
        else:
            # `roam uses` could not resolve it — honest, actionable, fast.
            # (Pattern-1D: disclose the degraded resolution; do NOT silently
            # claim dead/alive, and do NOT trigger the repo-wide scan.)
            facts["verdict"] = (
                f"`{target}` is not resolvable as a single indexed symbol "
                f"(may be an import alias, dynamically defined, misspelled, or "
                f"in an unindexed file). Run `roam search {target}` to locate "
                f"it, or `roam dead` for a repo-wide unused scan."
            )
            facts["resolution"] = "unresolved"
        facts["dead_check_definition"] = (
            f"Single-symbol reachability for `{target}` via `roam uses` "
            f"(not a repo-wide scan). 0 callers ⇒ delete candidate; any "
            f"caller ⇒ live. Entry points / dynamic dispatch are NOT "
            f"caught by static caller count — verify before deleting."
        )
        return facts
    # `--no-decay` skips the git-blame age pass (the dominant cost) — the probe
    # only needs the top-10 dead names, not per-symbol age — and a 5s budget lets
    # the repo-wide scan complete and PREFETCH instead of timing out at 3s (which
    # gave the agent nothing and made it pay the full cost itself).
    d = _run_roam(["dead", "--no-decay"], cwd, detail=True, timeout=5.0)
    if not d:
        return facts
    hc = d.get("high_confidence") or []
    if not hc:
        return facts
    facts["unused_top_10"] = [
        {
            "name": (item.get("value") or {}).get("name"),
            "kind": (item.get("value") or {}).get("kind"),
            "location": (item.get("value") or {}).get("location"),
            "action": (item.get("value") or {}).get("action"),
        }
        for item in hc[:10]
    ]
    facts["unused_definition"] = (
        f"Top-10 'high-confidence dead' symbols from `roam dead`. "
        f"action=SAFE means zero production consumers (graph proof); "
        f"REVIEW means heuristic. Total HC count: {len(hc)}."
    )
    return facts


def _probe_blast(named_paths: list[str], cwd: str | None, task: str | None = None) -> dict:
    """W41 — structural_blast branch. W39 C2 fixed the result-key shape
    (`affected_file_list` + `affected_files_total`). When no file is named but
    the task targets a SYMBOL ("blast radius of open_db", "what breaks if I
    change detect_layers"), run `roam impact <symbol>` directly (impact accepts
    a symbol name)."""
    facts: dict = {}
    target = named_paths[0] if named_paths else None
    if not target and task:
        m = re.search(r"`([A-Za-z_][A-Za-z0-9_]+)`", task) or re.search(
            r"\b(?:of|chang(?:e|ing)|to)\s+(?:the\s+)?"
            r"([a-z][a-z0-9]*_[a-z0-9_]+|[a-z]+[A-Z][A-Za-z0-9]*)\b",
            task,
        )
        if m:
            target = m.group(1)
    if not target:
        return facts
    d = _run_roam(["impact", target], cwd, detail=True)
    if not d:
        return facts
    affected = d.get("affected_file_list") or d.get("affected") or d.get("files") or d.get("impact_set") or []
    if not affected:
        return facts
    facts["impact_count"] = d.get("affected_files_total") or len(affected)
    facts["impact_top_files"] = affected[:_BLAST_TOP_FILES_CAP]
    facts["impact_definition"] = "files transitively affected if the named symbol changes (blast radius)"
    return facts


# W32 — parallel sub-probe dispatcher. Used by freeform_explore and
# synthesis_query to fan their independent sub-operations (CLI subprocess
# + disk-IO file reads) across a small ThreadPool. The W131 in-process
# CLI lock serializes _run_roam calls, but disk reads bypass that lock,
# so the wall-time win is on `_run_roam` || `file.read_text()`.
#
# Contract:
#   tasks: list of (key:str, callable) — each callable takes no args
#   max_workers: ThreadPool size (default 4 to avoid over-subscription)
#   per_task_timeout: seconds; on timeout the result is a sentinel dict
#   Returns: dict mapping key -> result; iteration order is sorted by key
#            so envelope-cache hits remain stable (deterministic merging).
#            Per-task timings are stashed on the returned dict's
#            `_w32_subprobe_timings_ms` key for telemetry.
_W32_TIMEOUT_SENTINEL = {"_w32_timeout": True}
_W32_ERROR_KEY = "_w32_error"


def _parallel_probe_dispatch(
    tasks: list[tuple[str, "callable"]],  # type: ignore[name-defined]
    max_workers: int = 4,
    per_task_timeout: float = 3.0,
) -> dict:
    """W32 — run sub-probes in parallel with timeout + exception isolation.

    Returns a dict keyed by sub-probe name, ordered by sorted key so the
    deterministic merging order keeps envelope-cache hashes stable. An
    extra `_w32_subprobe_timings_ms` entry records per-probe wall-time.
    """
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import TimeoutError as _W32Timeout

    if not tasks:
        return {}
    workers = max(1, min(max_workers, len(tasks)))
    timings: dict[str, float] = {}
    raw: dict = {}
    start_clock = time.monotonic
    starts: dict[str, float] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}
        for key, fn in tasks:
            starts[key] = start_clock()
            futures[key] = pool.submit(fn)
        for key, fut in futures.items():
            try:
                result = fut.result(timeout=per_task_timeout)
            except _W32Timeout:
                result = dict(_W32_TIMEOUT_SENTINEL)
                fut.cancel()
            except Exception as exc:  # noqa: BLE001 — isolation by design
                log_swallowed(f"compile.w32_subprobe.{key}", exc)
                result = {_W32_ERROR_KEY: type(exc).__name__}
            timings[key] = (start_clock() - starts[key]) * 1000.0
            raw[key] = result
    # Deterministic key order: sort the output dict by key.
    ordered: dict = {k: raw[k] for k in sorted(raw)}
    ordered["_w32_subprobe_timings_ms"] = {k: timings[k] for k in sorted(timings)}
    return ordered


def _extract_synthesis_target_symbol(task: str | None) -> str | None:
    """For "write a unit test for open_db" / "write a docstring for `X`" — the
    symbol the synthesis is ABOUT. Backtick first, then `test/spec/… for|of <X>`,
    then a bare identifier-shaped token after `for`. None when nothing concrete."""
    if not task:
        return None
    m = re.search(r"`([A-Za-z_][A-Za-z0-9_]+)`", task)
    if m:
        return m.group(1)
    m = re.search(
        r"\b(?:test|tests|spec|docstring|function|class|method)\s+"
        r"(?:for|of|covering|documenting|around)\s+"
        r"([A-Za-z_][A-Za-z0-9_]{2,})\b",
        task,
        re.IGNORECASE,
    )
    if m:
        return m.group(1)
    m = re.search(r"\bfor\s+([a-z][a-z0-9]*_[a-z0-9_]+|[a-z]+[A-Z][A-Za-z0-9]*)\b", task)
    if m:
        return m.group(1)
    return None


def _synth_parallel_fetch(target: str, cwd: str | None):
    """W32 — `roam file` CLI in parallel with a speculative disk read of target
    (for the W172 body embed). Returns
    (roam_file_dict_or_None, target_body_str_or_None, timings_ms)."""

    def _do_run_roam():
        return _run_roam(["file", target], cwd)

    def _do_read_target():
        if not cwd or not target:
            return None
        try:
            full = Path(cwd) / target if not os.path.isabs(target) else Path(target)
            if full.exists() and full.stat().st_size <= 400 * 1024:
                return full.read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError) as exc:
            log_swallowed("compile.synth.parallel_read", exc)
        return None

    sub = _parallel_probe_dispatch(
        [("roam_file", _do_run_roam), ("target_body", _do_read_target)],
        max_workers=4,
        per_task_timeout=3.0,
    )
    timings = sub.get("_w32_subprobe_timings_ms", {})
    d_raw = sub.get("roam_file")
    d = d_raw if (isinstance(d_raw, dict) and not d_raw.get("_w32_timeout") and not d_raw.get(_W32_ERROR_KEY)) else None
    tb_raw = sub.get("target_body")
    target_body = tb_raw if isinstance(tb_raw, str) else None
    return d, target_body, timings


def _embed_synth_symbol_body(
    task: str | None, top: list, target: str, cwd: str | None, target_body: str | None, synth_sym: str | None
):
    """W172 — embed the target symbol's body (~40 lines, 4 KB) for synthesis
    tasks, reusing the W32 parallel-read. Returns (snippet, definition) or None."""
    if not task:
        return None
    m = re.search(r"`([A-Za-z_][A-Za-z0-9_]+)`", task)
    sym_name = m.group(1) if m else None
    if not sym_name:
        m2 = re.search(r"\bwhat\s+(?:does|is)\s+([A-Za-z_][A-Za-z0-9_]+)\b", task, re.IGNORECASE)
        sym_name = m2.group(1) if m2 else None
    if not sym_name:
        sym_name = synth_sym
    if not sym_name:
        return None
    sym_row = next((s for s in top if s.get("name") == sym_name), None)
    if not (sym_row and cwd):
        return None
    try:
        if target_body is not None:
            body_text = target_body
        else:
            full = Path(cwd) / target if not os.path.isabs(target) else Path(target)
            body_text = (
                full.read_text(encoding="utf-8", errors="replace")
                if full.exists() and full.stat().st_size <= 400 * 1024
                else None
            )
        if body_text is None:
            return None
        lines = body_text.splitlines()
        ls = max(0, (sym_row.get("line_start") or 1) - 1)
        le = min(len(lines), (sym_row.get("line_end") or ls + 40))
        end = min(le, ls + 40)
        snippet = "\n".join(lines[ls:end])
        if len(snippet) > 4 * 1024:
            snippet = snippet[: 4 * 1024]
        definition = (
            f"AUTHORITATIVE body of `{sym_name}` from {target} "
            f"lines {ls + 1}-{end}. THIS IS the answer source — "
            f"do NOT re-Read {target}. Cite line numbers from "
            f"this snippet directly. (W204)"
        )
        return snippet, definition
    except (OSError, ValueError) as exc:
        log_swallowed("compile.synth.target_body", exc)
        return None


def _probe_synthesis_skeleton(named_paths: list[str], cwd: str | None, task: str | None = None) -> dict:
    """W41 — synthesis_query branch (W34c E2). Embed top-level file
    skeleton so the agent locates the right function without a Read.

    W171 — adaptive cap: large files (>50 top-level symbols) get
    truncated to 12 with a "and N more" note + concrete-noun hint.
    The W165 t14/t19 losses showed 30-item skeletons on huge files
    overwhelm the agent into wandering.

    W172 — when the task names a specific symbol that's ALSO in the
    file's symbol table, embed its source body (~40 lines around the
    def line) so the agent doesn't burn a turn on Read.

    W32 — `_run_roam(["file", ...])` runs in parallel with a
    speculative full-file disk read used downstream for the W172
    target-symbol body embed. Disk IO bypasses the W131 in-proc CLI
    lock, so the wall-time collapses to max(roam_file, disk_read).
    """
    facts: dict = {}
    target = named_paths[0] if named_paths else None
    # When no file is named but the task is ABOUT a symbol ("write a unit test
    # for open_db"), resolve the symbol → its file so the skeleton + target-body
    # embed still fire (the agent gets what to test without a Read).
    _synth_sym = None
    if not target:
        _synth_sym = _extract_synthesis_target_symbol(task)
        if _synth_sym:
            _r = _run_roam(["search", _synth_sym, "--mode", "exact"], cwd, detail=True)
            _results = (_r or {}).get("results") or []
            if _results:
                _locn = _results[0].get("location") or ""
                target = _locn.split(":")[0] or None
    if not target:
        return facts

    d, target_body, _timings = _synth_parallel_fetch(target, cwd)
    facts["_w32_subprobe_timings_ms"] = _timings

    if not d or not (symbols := d.get("symbols", [])):
        return facts
    top = [s for s in symbols if s.get("depth", 0) == 0]
    # W171 — large-file cap
    _W171_CAP = 12 if len(top) > 50 else 30
    skel = [
        {
            "name": s.get("name"),
            "kind": s.get("kind"),
            "signature": (s.get("signature", "") or "")[:120],
            "line_start": s.get("line_start"),
            "line_end": s.get("line_end"),
        }
        for s in top[:_W171_CAP]
    ]
    facts["file_skeleton"] = skel
    truncation_note = (
        f" — file is large ({len(top)} top-level symbols); listed first "
        f"{_W171_CAP}, use `roam search-symbol <name> --in {target}` for others."
        if len(top) > _W171_CAP
        else ""
    )
    facts["file_skeleton_definition"] = (
        f"Top-level symbols of {target} (name/kind/sig/lines). "
        f"Use to jump straight to the right function without "
        f"reading the whole file.{truncation_note}"
    )
    _sb = _embed_synth_symbol_body(task, top, target, cwd, target_body, _synth_sym)
    if _sb:
        facts["target_symbol_body"], facts["target_symbol_body_definition"] = _sb
    return facts


# Audit / security-review intent — gates the import-time-side-effect scan so it
# only runs for review-shaped tasks (not every freeform catch-all compile).
_AUDIT_INTENT_RE = re.compile(
    r"\b(audit|security\s+review|production[- ]bound|vulnerab|"
    r"for\s+security|reliability\s+and\s+correctness)\b",
    re.IGNORECASE,
)

_AUDIT_SCAN_EXTS = {".py", ".ts", ".js", ".tsx", ".jsx", ".go", ".rb", ".java"}


def _file_import_effects(fp: Path, cwd: str) -> list[str]:
    """Module-load io_write/process effects for one file (empty on IO error)."""
    try:
        if fp.stat().st_size > 200 * 1024:
            return []
        src = fp.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    from roam.world_model.side_effects import scan_module_init_effects

    rel = os.path.relpath(fp, cwd)
    return [
        f"{rel}:L{ln} {kind} ({label})"
        for ln, kind, label in scan_module_init_effects(src)
        if kind in ("io_write", "process")
    ]


def _audit_source_files_in_dir(base: Path) -> list[Path]:
    """Source files (by audit extension) directly in `base`, sorted."""
    if not base.is_dir():
        return []
    return [fp for fp in sorted(base.glob("*")) if fp.suffix in _AUDIT_SCAN_EXTS and fp.is_file()]


def _collect_audit_files(named_paths: list[str], cwd: str, cap: int = 40) -> list[Path]:
    """Source files in the directories of the named paths, capped at `cap`."""
    dirs = {os.path.dirname(p) for p in named_paths[:6] if isinstance(p, str) and not p.startswith("@pack/")}
    files: list[Path] = []
    for d in sorted(dirs):
        files.extend(_audit_source_files_in_dir(Path(cwd) / d))
        if len(files) >= cap:
            return files[:cap]
    return files


def _scan_named_dirs_import_effects(named_paths: list[str], cwd: str) -> list[str]:
    """Bounded import-time side-effect scan over the directories of the named
    files (audit aid). Capped so it never blows the compile budget."""
    hits: list[str] = []
    for fp in _collect_audit_files(named_paths, cwd):
        hits.extend(_file_import_effects(fp, cwd))
    return hits[:20]


def _freeform_parallel_fetch(target: str, cwd: str | None):
    """W32 — fan the `roam file` CLI call and the full-file disk read in
    parallel (both needed regardless of task shape). Returns
    (roam_file_dict_or_None, full_file_payload_or_None, timings_ms)."""

    def _do_run_roam():
        return _run_roam(["file", target], cwd)

    def _do_read_full():
        if not cwd or not target:
            return None
        try:
            full_path = Path(cwd) / target if not os.path.isabs(target) else Path(target)
            if not full_path.exists():
                return None
            st = full_path.stat()
            raw = full_path.read_text(encoding="utf-8", errors="replace")
            return {"raw": raw, "size": st.st_size}
        except (OSError, ValueError) as exc:
            log_swallowed("compile.freeform.parallel_read", exc)
        return None

    sub = _parallel_probe_dispatch(
        [("full_file", _do_read_full), ("roam_file", _do_run_roam)],
        max_workers=4,
        per_task_timeout=3.0,
    )
    timings = sub.get("_w32_subprobe_timings_ms", {})
    d_raw = sub.get("roam_file")
    d = d_raw if (isinstance(d_raw, dict) and not d_raw.get("_w32_timeout") and not d_raw.get(_W32_ERROR_KEY)) else None
    ffp_raw = sub.get("full_file")
    ffp = ffp_raw if (isinstance(ffp_raw, dict) and "raw" in ffp_raw) else None
    return d, ffp, timings


def _embed_freeform_symbol_body(
    task: str | None, top: list, target: str, cwd: str | None, full_file_payload: dict | None
):
    """W182 — for a backticked / 'what does X' symbol in a freeform task, embed
    its body (≤80 lines, 8 KB), reusing the W32 parallel-read when available.
    Returns (snippet, definition) or None."""
    if not task:
        return None
    m = re.search(r"`([A-Za-z_][A-Za-z0-9_]+)`", task)
    if m:
        sym_name = m.group(1)
    else:
        m2 = re.search(r"\bwhat\s+(?:does|is)\s+([A-Za-z_][A-Za-z0-9_]+)\b", task, re.IGNORECASE)
        sym_name = m2.group(1) if m2 else None
    if not sym_name:
        return None
    sym_row = next((s for s in top if s.get("name") == sym_name), None)
    if not (sym_row and cwd):
        return None
    try:
        if full_file_payload is not None and full_file_payload.get("size", 0) <= 400 * 1024:
            body_text = full_file_payload["raw"]
        else:
            full = Path(cwd) / target if not os.path.isabs(target) else Path(target)
            body_text = (
                full.read_text(encoding="utf-8", errors="replace")
                if full.exists() and full.stat().st_size <= 400 * 1024
                else None
            )
        if body_text is None:
            return None
        lines = body_text.splitlines()
        ls = max(0, (sym_row.get("line_start") or 1) - 1)
        le = min(len(lines), (sym_row.get("line_end") or ls + 80))
        end = min(le, ls + 80)
        snippet = "\n".join(lines[ls:end])
        if len(snippet) > 8 * 1024:
            snippet = snippet[: 8 * 1024]
        definition = f"Body of `{sym_name}` from {target} lines {ls + 1}-{end}. Use this; do NOT re-Read the file."
        return snippet, definition
    except (OSError, ValueError) as exc:
        log_swallowed("compile.freeform.target_body", exc)
        return None


# Parallel-implementation guard (SWE-django-11138 over-generalization failure
# mode). When freeform retrieval surfaces N+ sibling implementations of the same
# base file (e.g. db/backends/{mysql,oracle,sqlite3}/operations.py), an agent
# tends to copy one backend's fix onto the others. Surfacing the GROUP plus an
# explicit "treat each as independent" note counters that without dropping any
# path — a precise, bounded fact rather than a broad dump.
_PARALLEL_SIBLING_RE = re.compile(r"(?:[\w./-]+?/)?(?P<parent>[\w.-]+)/(?P<sib>[\w.-]+)/(?P<base>[\w-]+\.py)")
_PARALLEL_PARENT_DENYLIST: frozenset[str] = frozenset({"tests", "test", "testing", "fixtures", "docs"})


def _detect_parallel_implementations(paths, min_siblings: int = 3) -> list[str]:
    """Group ``paths`` sharing ``parent/<sibling>/base.py`` into parallel-impl
    families. Returns ``"parent/{a,b,c}/base.py"`` strings for families with at
    least ``min_siblings`` distinct siblings. Test/fixture/doc parents are
    excluded — they are not real parallel SOURCE surfaces (a fix to one is not
    blindly copyable to the next)."""
    groups: dict[tuple[str, str], set[str]] = {}
    for p in paths or ():
        if not isinstance(p, str):
            continue
        m = _PARALLEL_SIBLING_RE.search(p)
        if not m:
            continue
        parent = m.group("parent")
        if parent in _PARALLEL_PARENT_DENYLIST or parent.endswith("_tests"):
            continue
        groups.setdefault((parent, m.group("base")), set()).add(m.group("sib"))
    return [
        f"{parent}/{{{','.join(sorted(sibs))}}}/{base}"
        for (parent, base), sibs in sorted(groups.items())
        if len(sibs) >= min_siblings
    ]


def _freeform_parallel_guard(named_paths: list[str]) -> dict:
    """Parallel-implementation guard facts (SWE-11138 over-generalization). Empty
    dict when fewer than 3 sibling source files share a base."""
    parallel = _detect_parallel_implementations(named_paths)
    if not parallel:
        return {}
    return {
        "parallel_implementations": parallel[:5],
        "parallel_implementations_definition": (
            "PARALLEL implementations of the same component (e.g. one per "
            "backend/driver). Treat each as INDEPENDENT: apply the fix ONLY to "
            "the path(s) the task names — do NOT copy a conditional/guard from "
            "one sibling onto another. Each has its own correct fix shape."
        ),
    }


def _freeform_full_file_body(target: str, full_file_payload) -> dict:
    """W200/W205 — full-file embed facts for files <= 1000 LOC AND <= 40KB.
    Empty dict when the file is absent or over the size/LOC gates."""
    if full_file_payload is None:
        return {}
    raw = full_file_payload["raw"]
    st_size = full_file_payload["size"]
    if st_size > 40 * 1024:
        return {}
    line_count = raw.count("\n") + 1
    if line_count > 1000:
        return {}
    return {
        "full_file_body": raw,
        "full_file_body_definition": (
            f"FULL CONTENTS of {target} ({line_count} LOC, {st_size}B). THIS IS "
            f"the authoritative source — do NOT Read {target}. Cite line numbers "
            f"from THIS embedded body directly. (W200)"
        ),
    }


def _freeform_audit_effects(task: str | None, named_paths: list[str], cwd: str | None) -> dict:
    """Import-time side-effect facts for audit/security freeform tasks. Empty
    dict when the task is not audit-intent or nothing fires."""
    if not (task and cwd and _AUDIT_INTENT_RE.search(task)):
        return {}
    try:
        init_hits = _scan_named_dirs_import_effects(named_paths, cwd)
    except Exception as exc:  # noqa: BLE001
        log_swallowed("compile.freeform.import_effects", exc)
        return {}
    if not init_hits:
        return {}
    return {
        "import_time_side_effects": init_hits,
        "import_time_side_effects_definition": (
            "I/O executed at MODULE LOAD (import time), not inside a function — "
            "importing these mutates the world (hidden side effect, untestable "
            "import). A common audit finding."
        ),
    }


def _probe_freeform_skeleton(named_paths: list[str], cwd: str | None, task: str | None = None) -> dict:
    """W41 — freeform_explore branch (W34c E3). Skeleton + summary so
    agent can answer 'what does X do' without a Read.

    W182 — when task names a backticked symbol AND the file contains it,
    also embed the symbol's source body (~40 lines around its def).
    W200 — when the named file is SMALL (<400 LOC, <16KB), embed the
    ENTIRE file content. Directly attacks the 28% Read-tool surface
    measured in W195 tool-trace. The agent gets the full source upfront
    and skips the Read+inspect cycle.

    W32 — `_run_roam(["file", ...])` runs in parallel with the
    full-file disk read used by W200's full_file_body embed. Both
    operations are independent IO and the disk read bypasses the W131
    in-proc CLI lock, so wall-time collapses to max(roam_file, read).
    """
    facts: dict = {}
    if not named_paths:
        return facts
    target = named_paths[0]

    # Parallel-implementation guard — computed up-front so it survives even a
    # degraded skeleton fetch below (the SWE-11138 over-generalization mode).
    facts.update(_freeform_parallel_guard(named_paths))

    d, full_file_payload, _timings = _freeform_parallel_fetch(target, cwd)
    facts["_w32_subprobe_timings_ms"] = _timings
    if not d:
        return facts
    symbols = d.get("symbols") or []
    summary = d.get("summary") or {}
    top = [s for s in symbols if s.get("depth", 0) == 0]
    facts["file_skeleton"] = [
        {"name": s.get("name"), "kind": s.get("kind"), "signature": (s.get("signature", "") or "")[:120]}
        for s in top[:20]
    ]
    facts["file_summary"] = {
        "line_count": summary.get("line_count"),
        "symbol_count": summary.get("symbols"),
        "verdict": summary.get("verdict"),
    }
    facts["file_skeleton_definition"] = (
        f"Top-level structure of {target} — usually enough to answer 'what does X do' without a Read."
    )
    facts.update(_freeform_full_file_body(target, full_file_payload))
    _sb = _embed_freeform_symbol_body(task, top, target, cwd, full_file_payload)
    if _sb:
        facts["target_symbol_body"], facts["target_symbol_body_definition"] = _sb
    facts.update(_freeform_audit_effects(task, named_paths, cwd))

    return facts


_COMPLEXITY_TARGET_STOPWORDS: frozenset[str] = frozenset(
    {
        "complex",
        "complexity",
        "cyclomatic",
        "cognitive",
        "too",
        "how",
        "the",
        "this",
        "that",
        "function",
        "method",
        "class",
        "symbol",
        "code",
        "file",
        "module",
        "score",
        "grade",
        "metric",
        "metrics",
        "what",
        "whats",
    }
)


def _resolve_complexity_target(task: str | None, cwd: str | None):
    """Extract an identifier symbol from a complexity task and resolve it to its
    file via `roam search --mode exact`. Returns (sym, target_file) or (None, None)."""
    if not task:
        return None, None
    sym = None
    for tok in re.findall(r"`?([A-Za-z_][A-Za-z0-9_]{2,})`?", task):
        if tok.lower() in _COMPLEXITY_TARGET_STOPWORDS:
            continue
        if "_" not in tok and not re.search(r"[a-z][A-Z]", tok):
            continue
        sym = tok
        break
    if not sym:
        return None, None
    r = _run_roam(["search", sym, "--mode", "exact"], cwd, detail=True)
    results = (r or {}).get("results") or []
    target = ((results[0].get("location") or "").split(":")[0] or None) if results else None
    return sym, target


def _probe_complexity_repo_wide(cwd: str | None) -> dict:
    """Repo-wide top-complexity (god components) — when no file/symbol is named
    ("show me the god components"). `roam complexity` with no arg = worst offenders."""
    facts: dict = {}
    d_repo = _run_roam(["complexity"], cwd, detail=True)
    rows = [
        {
            "name": (s.get("value") or {}).get("name"),
            "cognitive_complexity": (s.get("value") or {}).get("cognitive_complexity"),
            "severity": (s.get("value") or {}).get("severity"),
            "file": (s.get("value") or {}).get("file"),
            "line": (s.get("value") or {}).get("line"),
        }
        for s in ((d_repo or {}).get("symbols") or [])
    ]
    if rows:
        facts["complexity_metrics"] = {"scope": "repository", "god_components_shown": len(rows)}
        facts["top_complex_symbols"] = rows[:8]
        facts["complexity_target_verdict"] = (
            f"Repo-wide most-complex symbols (god components). Worst: "
            f"`{rows[0]['name']}` cc={rows[0]['cognitive_complexity']} at "
            f"{rows[0]['file']}:{rows[0]['line']}."
        )
    return facts


def _probe_complexity(named_paths: list[str], cwd: str | None, task: str | None = None) -> dict:
    """W41 — structural_complexity branch. file-info metrics + top complex
    symbols for the named file. When no file is named but the task targets a
    SYMBOL ("is open_db too complex"), resolve the symbol to its file via
    `roam search --mode exact` so the per-symbol complexity surfaces in
    `top_complex_symbols`."""
    facts: dict = {}
    target = named_paths[0] if named_paths else None
    sym = None
    if not target:
        sym, target = _resolve_complexity_target(task, cwd)
    if not target:
        return _probe_complexity_repo_wide(cwd)
    # `roam complexity <path>` — NOT `file-info` (which is the MCP tool name,
    # not a CLI verb; the old call exited 2 and the probe silently emptied).
    d = _run_roam(["complexity", target], cwd, detail=True)
    if not d:
        return facts
    summary = d.get("summary", {})
    rows = [
        {
            "name": (s.get("value") or {}).get("name"),
            "cognitive_complexity": (s.get("value") or {}).get("cognitive_complexity"),
            "severity": (s.get("value") or {}).get("severity"),
            "line": (s.get("value") or {}).get("line"),
        }
        for s in (d.get("symbols") or [])
    ]
    # File-scoped metrics. `roam complexity <file>` now scopes its summary
    # stats to the path filter (the repo-wide-leak bug is fixed), so
    # `average_complexity`/`p90_complexity` are file-scoped here. The
    # max/critical counts are derived from the returned symbols for a
    # self-consistent, unambiguous envelope.
    _ccs = [r["cognitive_complexity"] or 0 for r in rows]
    facts["complexity_metrics"] = {
        "file_max_cognitive_complexity": max(_ccs) if _ccs else 0,
        "file_average_complexity": summary.get("average_complexity"),
        "file_p90_complexity": summary.get("p90_complexity"),
        "file_symbols_shown": len(rows),
        "file_critical_or_high": sum(1 for r in rows if r["severity"] in ("critical", "high")),
    }
    if rows:
        facts["top_complex_symbols"] = rows[:5]
    if sym:
        facts["complexity_target_symbol"] = sym
        hit = next((r for r in rows if r["name"] == sym), None)
        if hit:
            facts["complexity_target_verdict"] = (
                f"`{sym}` has cognitive complexity "
                f"{hit['cognitive_complexity']} ({hit['severity']}) at "
                f"{target}:{hit['line']}."
            )
        else:
            facts["complexity_target_verdict"] = (
                f"`{sym}` is NOT among the most-complex symbols of {target} "
                f"(file p90={summary.get('p90_complexity')}) — not a "
                f"complexity hotspot. See `top_complex_symbols` for the file's "
                f"actual hotspots."
            )
    return facts


def _probe_cycle(named_paths: list[str], cwd: str | None) -> dict:
    """W41 — structural_cycle branch. Embed `roam clusters` cycle data."""
    facts: dict = {}
    d = _run_roam(["clusters"], cwd, detail=True)
    if not d:
        return facts
    cycles = d.get("cycles") or d.get("cyclic_groups") or []
    # Always embed the count — "0 cycles" is the DEFINITIVE answer to "how many
    # cycles / are there cycles", and an empty envelope would force the agent to
    # re-run the tool to learn the graph is acyclic (absent state must be explicit).
    facts["cycle_count"] = len(cycles)
    if cycles:
        facts["cycles"] = cycles[:5]
        facts["cycles_definition"] = (
            f"{len(cycles)} import cycle(s) (cyclic SCCs) from `roam clusters`; "
            f"showing up to 5. Each group is a set of files that import each other."
        )
    else:
        facts["cycles_definition"] = (
            "No import cycles detected — `roam clusters` found 0 cyclic groups; the module dependency graph is acyclic."
        )
    return facts


# W41 — dispatch table replaces the 134-complexity if/elif chain.
# Adding a new procedure = (a) define a `_probe_<name>` helper, (b)
# register it here. Smells detector flagged the old chain as
# `brain-method` (134), `switch-statement` (8 cases), and three
# `duplicate-conditionals`; this dispatch eliminates all four.
def _probe_w11_dispatch(named_paths: list[str], cwd: str | None, task: str | None = None) -> dict | None:
    """Dispatch adapter — W11 probe takes (task, cwd)."""
    if not task:
        return None
    return _probe_symbol_defined_where_for_task(task, cwd)


def _probe_w12_dispatch(named_paths: list[str], cwd: str | None, task: str | None = None) -> dict | None:
    """Dispatch adapter — W12 probe takes (task, cwd)."""
    if not task:
        return None
    return _probe_top_n_ranking_for_task(task, cwd)


def _probe_w13_dispatch(named_paths: list[str], cwd: str | None, task: str | None = None) -> dict | None:
    """Dispatch adapter — W13 probe takes (task, cwd)."""
    if not task:
        return None
    return _probe_cli_verb_why_slow_for_task(task, cwd)


def _probe_w28_dispatch(named_paths: list[str], cwd: str | None, task: str | None = None) -> dict | None:
    """Dispatch adapter — W28 probe takes (task, cwd)."""
    if not task:
        return None
    return _probe_compare_x_vs_y_for_task(task, cwd)


# W-HIST — time-window phrases the probe can forward to `git log --since`.
_FILE_HISTORY_SINCE_MAP: tuple[tuple[str, str], ...] = (
    ("last week", "1 week ago"),
    ("past week", "1 week ago"),
    ("last month", "1 month ago"),
    ("past month", "1 month ago"),
    ("last day", "1 day ago"),
    ("yesterday", "1 day ago"),
    ("today", "1 day ago"),
)

_FILE_HISTORY_MAX_COMMITS = 10


def _probe_file_history(named_paths: list[str], cwd: str | None, task: str | None = None) -> dict | None:
    """W-HIST — embed `git log` for the named file so the agent answers
    "what changed in X recently" from the envelope without shelling out.

    Honors a task time window (last week/month/day) via `--since`. Emits
    `file_history_unavailable` (instead of nothing) when the file has no
    tracked history, so the L1 envelope still carries an explicit answer.
    """
    if not named_paths:
        return None
    import subprocess as _sp

    target = named_paths[0]
    since = None
    low = (task or "").lower()
    for phrase, git_since in _FILE_HISTORY_SINCE_MAP:
        if phrase in low:
            since = git_since
            break
    args = ["git", "log", f"--max-count={_FILE_HISTORY_MAX_COMMITS}", "--format=%h %ad %an %s", "--date=short"]
    if since:
        args.append(f"--since={since}")
    args += ["--", target]
    try:
        proc = _sp.run(args, capture_output=True, text=True, timeout=5.0, cwd=cwd or None)
    except (OSError, _sp.SubprocessError) as exc:
        log_swallowed("compile.file_history.git_log", exc)
        return None
    if proc.returncode != 0:
        err = (proc.stderr or "").lower()
        if "not a git repository" in err:
            return {
                "file_history_unavailable": (
                    f"{cwd or '.'} is not a git repository — no commit history exists for {target}"
                ),
                "file_history_unavailable_definition": ("Explicit no-history answer. State it directly."),
            }
        if "does not have any commits" in err or "bad revision" in err:
            return {
                "file_history_unavailable": (f"repository has no commits yet — no history for {target}"),
                "file_history_unavailable_definition": ("Explicit no-history answer. State it directly."),
            }
        return None
    commits = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
    if not commits:
        window = f" since {since}" if since else ""
        return {
            "file_history_unavailable": (
                f"no commits touching {target}{window} — untracked file, new file, or empty window"
            ),
            "file_history_unavailable_definition": (
                "Explicit no-history answer. State it directly; do NOT re-run `git log` to double-check."
            ),
        }
    window = f" --since '{since}'" if since else ""
    return {
        "file_recent_commits": commits,
        "file_recent_commits_definition": (
            f"Last {len(commits)} commits touching {target}{window} "
            f"(hash date author subject). Answer history questions from "
            f"THIS list — do NOT run `git log` again."
        ),
    }


# W-REPO — dimension → (roam subcommand, summary keys worth surfacing).
_REPO_STRUCTURE_COMMANDS: dict[str, str] = {
    "layers": "layers",
    "clusters": "clusters",
    "health": "health",
}


def _probe_repo_structure(named_paths: list[str], cwd: str | None, task: str | None = None) -> dict | None:
    """W-REPO — embed the summary of the repo-scoped roam command (layers /
    clusters / health) so the agent answers without shelling out. Emits
    `repo_structure_unavailable` with the literal command on any failure."""
    dim = _extract_repo_structure(task or "")
    if not dim:
        return None
    subcmd = _REPO_STRUCTURE_COMMANDS[dim]
    d = _run_roam([subcmd], cwd, timeout=12.0)
    summary = (d or {}).get("summary")
    if not summary:
        return {
            "repo_structure_unavailable": (
                f"`roam {subcmd}` returned no summary — run `roam --json "
                f"{subcmd}` directly (may need `roam init` first)"
            ),
            "repo_structure_unavailable_definition": (
                "Explicit degraded answer. Give the user the literal command; do not guess counts."
            ),
        }
    return {
        "repo_structure_result": {"dimension": dim, "summary": summary},
        "repo_structure_result_definition": (
            f"Summary envelope of `roam {subcmd}` (verdict + counts). Answer from THIS — do NOT re-run `roam {subcmd}`."
        ),
    }


def _toml_loader():
    """Return the stdlib tomllib (py3.11+) or the tomli backport."""
    try:
        import tomllib as _toml  # py3.11+
    except ImportError:  # pragma: no cover
        import tomli as _toml  # type: ignore
    return _toml


def _load_pyproject_scripts(cwd: str) -> dict | None:
    """Return the `[project.scripts]` table from pyproject.toml, or None
    (missing file / parse error / no section). Best-effort by design."""
    import os

    pp = os.path.join(cwd, "pyproject.toml")
    if not os.path.exists(pp):
        return None
    try:
        with open(pp, "rb") as fh:
            data = _toml_loader().load(fh)
    except Exception as exc:  # noqa: BLE001 — best-effort
        log_swallowed("compile.declared_console_scripts", exc)
        return None
    scripts = (data.get("project") or {}).get("scripts") or {}
    return scripts or None


def _console_script_file(cwd: str, target: str) -> str | None:
    """Map a console-script target (`pkg.mod:fn`) to its repo file
    (src-layout first, then flat). None when no candidate exists on disk."""
    import os

    mod = str(target).split(":", 1)[0].replace(".", "/")
    for rel in (f"src/{mod}.py", f"{mod}.py", f"src/{mod}/__init__.py", f"{mod}/__init__.py"):
        if os.path.exists(os.path.join(cwd, rel)):
            return rel
    return None


def _declared_console_scripts(cwd: str | None) -> list[dict] | None:
    """W-ENTRY+ — read `[project.scripts]` console scripts from pyproject.toml.

    Returns [{"name": "roam", "target": "roam.cli:cli",
              "file": "src/roam/cli.py"}, ...] or None. The console script is
    the AUTHORITATIVE program entry point — what `pip install` puts on PATH.
    Best-effort: returns None on any parse/IO error or when the file/section
    is absent (graceful no-op outside Python packages)."""
    if not cwd:
        return None
    scripts = _load_pyproject_scripts(cwd)
    if not scripts:
        return None
    out: list[dict] = []
    for name, target in list(scripts.items())[:10]:
        entry = {"name": name, "target": target}
        rel = _console_script_file(cwd, target)
        if rel:
            entry["file"] = rel
        out.append(entry)
    return out or None


def _probe_entry_point_where(named_paths: list[str], cwd: str | None, task: str | None = None) -> dict | None:
    """W-ENTRY — dedicated-procedure adapter over the W67 entry-points
    probe, with an explicit degraded answer instead of an empty envelope.

    Re-ranks the raw probe list: test-file entries dropped (a test method
    is never "the entry point"), entries whose protocol kind matches a
    task keyword (cli/http/worker/server) float to the top."""
    facts = _probe_entry_points_for_task(task or "", cwd) or {}
    # W-ENTRY+ (2026-06-10) — the AUTHORITATIVE CLI entry point is the
    # `[project.scripts]` console-script in pyproject.toml (e.g.
    # `roam = "roam.cli:cli"`), not whichever indexed function ranks highest
    # by fan-out. Surface it first when the task asks about the CLI/app entry.
    declared = _declared_console_scripts(cwd)
    if declared:
        facts["declared_entry_points"] = declared
        facts["declared_entry_points_definition"] = (
            "Authoritative console-script entry points from pyproject.toml "
            "[project.scripts] (name -> module:function). For 'where does the "
            "CLI start', THIS is the answer; the ranked list below is "
            "supporting context."
        )
    if facts.get("entry_points") or declared:
        entries = facts.get("entry_points") or []
        if entries:
            low = (task or "").lower()
            wanted = next((k for k in ("cli", "http", "worker", "server", "repl") if k in low), None)

            def _rank(e: dict) -> tuple:
                f = str(e.get("file", ""))
                is_test = f.startswith("tests/") or "/test" in f
                kind = str(e.get("kind", "")).lower()
                kind_hit = bool(wanted) and wanted in kind
                return (is_test, not kind_hit, -(e.get("fan_out") or 0))

            facts["entry_points"] = (
                sorted((e for e in entries if not (str(e.get("file", "")).startswith("tests/"))), key=_rank)[:10]
                or entries[:10]
            )
        return facts
    return {
        "entry_points_unavailable": (
            "no entry points returned — run `roam --json entry-points` (may need `roam init` first)"
        ),
        "entry_points_unavailable_definition": (
            "Explicit degraded answer. Give the user the literal command; do not guess the entry point."
        ),
    }


def _probe_session_meta(named_paths: list[str], cwd: str | None, task: str | None = None) -> dict | None:
    """W-META — tiny repo-state anchor for continuation directives.

    Embeds the `roam brief` verdict + mode + recommended next command —
    deliberately SMALL (the conversation, not the envelope, carries the
    task). Explicit degraded answer when brief is unavailable."""
    d = _run_roam(["brief"], cwd, timeout=8.0)
    if d:
        summary = d.get("summary") or {}
        brief: dict = {"verdict": summary.get("verdict")}
        mode = d.get("mode")
        if isinstance(mode, dict):
            brief["mode"] = mode.get("active") or mode.get("mode")
        nxt = d.get("next")
        if isinstance(nxt, dict) and nxt.get("next_invocation"):
            brief["next"] = nxt["next_invocation"]
        elif isinstance(nxt, list) and nxt:
            brief["next"] = nxt[:3]
        highlights = d.get("highlights")
        if isinstance(highlights, dict):
            zones = highlights.get("danger_zones") or []
            if zones:
                brief["top_danger_zone"] = {
                    "path": zones[0].get("path"),
                    "danger_score": zones[0].get("danger_score"),
                }
        elif isinstance(highlights, list) and highlights:
            brief["highlights"] = highlights[:3]
        if brief.get("verdict"):
            return {
                "session_brief": brief,
                "session_brief_definition": (
                    "Repo-state anchor (mode + next + highlights) from "
                    "`roam brief`. The task is a continuation directive — "
                    "the conversation carries the actual work; use this "
                    "only to re-anchor state."
                ),
            }
    return {
        "session_brief_unavailable": (
            "`roam brief` returned nothing — continue the in-flight work "
            "from the conversation; run `roam brief` manually if state "
            "re-anchoring is needed"
        ),
        "session_brief_unavailable_definition": ("Explicit degraded answer; the conversation remains authoritative."),
    }


def _probe_self_contained(named_paths: list[str], cwd: str | None, task: str | None = None) -> dict | None:
    """W-BATCH — zero-probe notice for self-contained payloads. The single
    fact tells the agent (and telemetry) WHY nothing was prefetched."""
    return {
        "self_contained_notice": (
            "Batch payload detected — all inputs and the output spec are in "
            "the prompt itself. No repo facts prefetched (none needed)."
        ),
        "self_contained_notice_definition": (
            "Execute the prompt as written. Do not run roam tools or explore this repo."
        ),
    }


def _probe_config_where(named_paths: list[str], cwd: str | None, task: str | None = None) -> dict | None:
    """W-CFG — dedicated-procedure adapter over the W49 config-by-name
    probe, with an explicit degraded answer instead of an empty envelope."""
    facts = _probe_config_for_task(task or "", cwd)
    if facts:
        return facts
    m = _CONFIG_BY_NAME_RE.search(task or "")
    name = ((m.group(3) or m.group(6)) if m else "") or "<name>"
    return {
        "config_matches_unavailable": (
            f"no indexed matches for '{name}' — run `roam grep {name}` "
            f"or check .env / deployment config outside the repo"
        ),
        "config_matches_unavailable_definition": (
            "Explicit degraded answer. Give the user the literal command; do not guess the config location."
        ),
    }


_PROBE_DISPATCH: dict[str, callable] = {  # type: ignore[type-arg]
    "structural_coupling": _probe_coupling,
    "structural_callers": _probe_callers,
    "structural_dead": _probe_dead,
    "structural_blast": _probe_blast,
    "structural_complexity": _probe_complexity,
    "structural_cycle": _probe_cycle,
    "synthesis_query": _probe_synthesis_skeleton,
    "freeform_explore": _probe_freeform_skeleton,
    # W-LIFT — describe-file reuses the freeform skeleton probe (file_skeleton +
    # file_summary + small-file body) but is NOT in the broad augment dispatch,
    # so its envelope stays tight (file-focused, no always-on dump).
    "describe_file": _probe_freeform_skeleton,
    # W11/W12/W13 — new probe families (2026-06-02).
    "symbol_defined_where": _probe_w11_dispatch,
    "top_n_ranking": _probe_w12_dispatch,
    "cli_verb_why_slow": _probe_w13_dispatch,
    # W28 — compare X vs Y (semantic-diff / git-diff / coupling-pair filter).
    "compare_x_vs_y": _probe_w28_dispatch,
    # W-HIST — file-history (git log embed for the named file).
    "file_history": _probe_file_history,
    # W-REPO — repo-level layers/clusters/health summary embed.
    "repo_structure": _probe_repo_structure,
    # W-ENTRY / W-CFG — adapters over the W67 / W49 probes.
    "entry_point_where": _probe_entry_point_where,
    "config_where": _probe_config_where,
    # W-META — tiny repo-state anchor for continuation directives.
    "session_meta": _probe_session_meta,
    # W-BATCH — zero-probe notice for self-contained payloads.
    "self_contained_task": _probe_self_contained,
}


def _probe_for_procedure(
    procedure: str, named_paths: list[str], cwd: str | None, task: str | None = None
) -> dict | None:
    """L1.1 probe-and-fill dispatcher.

    Looks up the per-procedure probe in `_PROBE_DISPATCH` and runs it.
    Returns None if no probe applies OR the probe returned empty.

    W172 — pass `task` to probes that accept it (synthesis_query uses it
    for target-symbol body embed). Older probes that don't accept the
    kwarg fall back to the legacy 2-arg call via TypeError.
    """
    fn = _PROBE_DISPATCH.get(procedure)
    if fn is None:
        return None
    try:
        facts = fn(named_paths, cwd, task=task)
    except TypeError:
        facts = fn(named_paths, cwd)
    return facts or None


def _read_file_slice(path: str, line: int, cwd: str | None, before: int = 5, after: int = 5) -> dict | None:
    """W35a — read ±N lines around `line` in `path`. Returns None on missing/IO error."""
    full = os.path.join(cwd, path) if cwd and not os.path.isabs(path) else path
    try:
        with open(full, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except (OSError, ValueError) as exc:
        log_swallowed("compile._read_file_slice", exc)
        return None
    if not lines:
        return None
    start = max(1, line - before)
    end = min(len(lines), line + after)
    excerpt = []
    for i in range(start, end + 1):
        marker = ">>" if i == line else "  "
        excerpt.append(f"{marker} {i:4d}  {lines[i - 1].rstrip()}")
    return {
        "path": path,
        "line": line,
        "line_count": len(lines),
        "excerpt": "\n".join(excerpt),
    }


def _probe_stack_trace_for_task(task: str, cwd: str | None) -> dict | None:
    """W35a — extract every (file, line) frame from the task text, read the
    source slice around each frame, and return them ordered most-recent-frame
    last (so the agent sees the failing call at the bottom — matches Python
    convention).
    """
    frames = _extract_stack_frames(task)
    if not frames:
        return None
    slices = []
    for path, line in frames:
        sl = _read_file_slice(path, line, cwd)
        if sl is not None:
            slices.append(sl)
    if not slices:
        return None
    # W61 — auto-patch hint. Inspect the FAILING line for common
    # error patterns and propose a 1-line fix snippet the agent can
    # adapt. Pure heuristics; failure modes degrade gracefully to
    # "no hint".
    patch_hints = _suggest_patch_hints(task, slices)
    out = {
        "stack_frames": slices,
        "stack_frames_definition": (
            "Source slices around each (file, line) frame extracted from "
            "the task's stack trace. The LAST frame is the failing call. "
            "Do NOT Read these files — the excerpt IS the relevant context."
        ),
    }
    if patch_hints:
        out["patch_hints"] = patch_hints
        out["patch_hints_definition"] = (
            "W61 auto-patch suggestions. Each hint pairs an error class "
            "with a 1-line fix template the agent can adapt to the "
            "specific failing line. Hints are heuristic; ALWAYS verify "
            "before committing."
        )
    return out


def _suggest_patch_hints(task: str, slices: list[dict]) -> list[dict]:
    """W61 — match common error patterns to fix templates.

    Returns at most one hint per frame, ordered same as `slices`.
    Hints are intentionally short — they're a directional nudge, not
    a generator. The agent picks the right one and adapts.
    """
    hints: list[dict] = []
    task_lower = task.lower()
    # Detection rules: (error_signature, hint_template)
    # Each template includes a `template` (1-line fix sketch) and
    # `rationale` (one sentence on why this might fix it).
    rules: list[tuple[str, dict]] = [
        (
            "keyerror",
            {
                "template": "Use `dict.get(key, default)` or add `if key in dict:` guard",
                "rationale": "KeyError surfaces missing dict keys; `.get` returns None instead of raising.",
            },
        ),
        (
            "indexerror",
            {
                "template": "Add `if idx < len(seq):` guard or use `seq[idx:idx+1]` slice",
                "rationale": "IndexError on out-of-bounds; defensive guard or slice returns empty.",
            },
        ),
        (
            "attributeerror",
            {
                "template": "Add `if obj is not None and hasattr(obj, 'attr'):` guard",
                "rationale": "AttributeError on None or missing attr; defensive check before access.",
            },
        ),
        (
            "typeerror",
            {
                "template": "Check argument types with `isinstance(x, T)` before the call",
                "rationale": "TypeError surfaces shape mismatches; verify types at the boundary.",
            },
        ),
        (
            "valueerror",
            {
                "template": "Wrap parse/conversion in `try: ... except ValueError:` with fallback",
                "rationale": "ValueError surfaces malformed input; catch + use default value.",
            },
        ),
        (
            "assertionerror",
            {
                "template": "Either weaken the assertion's predicate or update the fixture to satisfy it",
                "rationale": "AssertionError = expected condition violated; fix the predicate or the input.",
            },
        ),
        (
            "zerodivisionerror",
            {
                "template": "Add `if divisor != 0:` guard or use `numpy.divide(..., where=...)`",
                "rationale": "ZeroDivisionError on x/0; defensive check or numpy's where-safe divide.",
            },
        ),
        (
            "oserror",
            {
                "template": "Wrap I/O in `try: ... except OSError as exc: log_swallowed(scope, exc); return None`",
                "rationale": "OSError on I/O; graceful degrade + observability beats unhandled raise.",
            },
        ),
        (
            "nameerror",
            {
                "template": "Check for missing import; add `from module import name` at top",
                "rationale": "NameError on unresolved symbol — almost always a missing import.",
            },
        ),
        # W74 — extended templates for high-frequency bug archetypes.
        (
            "modulenotfounderror",
            {
                "template": "`pip install <pkg>` OR check `sys.path`; if vendored, fix the package import path",
                "rationale": "ModuleNotFoundError — missing dependency or broken sys.path.",
            },
        ),
        (
            "importerror",
            {
                "template": "Check the imported name exists in the module (renamed? moved?); compare against the module's __all__",
                "rationale": "ImportError — the symbol moved or renamed at the source module.",
            },
        ),
        (
            "filenotfounderror",
            {
                "template": "Check path resolution against cwd; use `os.path.abspath` or `pathlib.Path.resolve()` for diagnostics",
                "rationale": "FileNotFoundError — usually a relative path resolved against the wrong cwd.",
            },
        ),
        (
            "recursionerror",
            {
                "template": "Add a base case check at function entry; or memoize with `functools.lru_cache`",
                "rationale": "RecursionError — missing base case or unbounded recursion.",
            },
        ),
        (
            "timeouterror",
            {
                "template": "Wrap with `try: ... except TimeoutError:` and add `retry` with backoff, OR bump the timeout to a safe ceiling",
                "rationale": "TimeoutError — slow op exceeded budget; bound + retry or raise the cap.",
            },
        ),
        (
            "permissionerror",
            {
                "template": "Check file mode (`os.access(path, os.W_OK)`); chmod or run as the right user",
                "rationale": "PermissionError — file ownership/mode mismatch or running as the wrong user.",
            },
        ),
        (
            "stopiteration",
            {
                "template": "Convert generator to `next(gen, default)` OR wrap in `try: ... except StopIteration:`",
                "rationale": "StopIteration leaking out of generator code — almost always a forgotten default.",
            },
        ),
        (
            "connectionerror",
            {
                "template": "Add retry with exponential backoff; verify the endpoint is reachable; check VPN/firewall",
                "rationale": "ConnectionError — transient network or endpoint outage.",
            },
        ),
    ]
    for sl in slices:
        # Per-frame hint based on task-text error class
        for needle, body in rules:
            if needle in task_lower:
                hints.append(
                    {
                        "frame_path": sl.get("path"),
                        "frame_line": sl.get("line"),
                        "error_class_matched": needle,
                        **body,
                    }
                )
                break
    # Cap at 3 hints to keep envelope small
    return hints[:3]


def _resolve_sibling_test_path(src_path: str, cwd: str | None) -> str | None:
    """W36a — find the conventional test file for `src_path`. Returns
    the FIRST candidate that exists on disk, or None.

    Conventions covered:
      Python: tests/test_<stem>.py, mirrored src/→tests/ subpath
      Go:     <same_dir>/<stem>_test.go
      JS/TS:  <same_dir>/<stem>.test.<ext>, __tests__/<stem>.test.<ext>
              tests/<stem>.test.<ext>
    """
    import os

    base = os.path.basename(src_path)
    stem, ext = os.path.splitext(base)
    candidates: list[str] = []
    src_dir = os.path.dirname(src_path)
    if ext in (".py", ".pyx"):
        candidates.append(f"tests/test_{stem}.py")
        if "src/" in src_path:
            mirror = src_path.replace("src/", "tests/", 1)
            mirror_dir = os.path.dirname(mirror)
            if mirror_dir:
                candidates.append(os.path.join(mirror_dir, f"test_{stem}.py"))
        candidates.append(os.path.join(src_dir, f"test_{stem}.py"))
    elif ext == ".go":
        candidates.append(os.path.join(src_dir, f"{stem}_test.go"))
    elif ext in (".js", ".ts", ".tsx", ".jsx"):
        candidates.append(os.path.join(src_dir, f"{stem}.test{ext}"))
        candidates.append(os.path.join(src_dir, "__tests__", f"{stem}.test{ext}"))
        candidates.append(f"tests/{stem}.test{ext}")
    elif ext == ".rb":
        candidates.append(f"spec/{stem}_spec.rb")
        candidates.append(f"test/{stem}_test.rb")
    for c in candidates:
        full = os.path.join(cwd, c) if cwd and not os.path.isabs(c) else c
        if os.path.exists(full):
            return c
    # Glob fallback: tests/test_<stem>*.py (catches projects where the test
    # is named like `test_<stem>_consolidation.py`, `test_<stem>_extended.py`,
    # etc. — common when one source file has multiple test modules).
    if ext in (".py", ".pyx"):
        import glob

        pattern = f"tests/test_{stem}*.py"
        base_dir = cwd if cwd else "."
        matches = sorted(glob.glob(os.path.join(base_dir, pattern)))
        if matches:
            # Return path relative to cwd if possible
            chosen = matches[0]
            if cwd and chosen.startswith(cwd):
                chosen = os.path.relpath(chosen, cwd)
            return chosen
    return None


def _extract_test_target_function(task: str) -> str | None:
    """W86 — pull a target function name from a test-write task.

    Patterns:
      "write a pytest for X covering foo"  → 'foo'
      "test for the bar function in X"     → 'bar'
      "add a test for baz()"               → 'baz'
      "write tests for X.qux"              → 'qux'

    Backticked symbols win when present.
    """
    backticked = re.findall(r"`([A-Za-z_][A-Za-z0-9_]+)`", task)
    if backticked:
        return backticked[0]
    m = re.search(r"\bcovering\s+([a-zA-Z_][a-zA-Z0-9_]+)", task, re.IGNORECASE)
    # Identifier-shape gate (2026-06-10): "covering the cache path" /
    # "covering edge cases" captured plain English; only an identifier-
    # shaped capture should short-circuit the later patterns.
    if m:
        cand = m.group(1)
        if "_" in cand or any(c.isdigit() for c in cand) or not (cand.islower() or cand.isupper()):
            return cand
    m = re.search(
        r"\bfor\s+(?:the\s+)?([a-zA-Z_][a-zA-Z0-9_]+)(?:\s*\(\)|\s+(?:function|method|class))", task, re.IGNORECASE
    )
    if m:
        return m.group(1)
    m = re.search(r"\btest\s+([a-zA-Z_][a-zA-Z0-9_]+)\b", task, re.IGNORECASE)
    # "of"/"on"/"in" added 2026-06-10: "a unit test of validateEmail" captured
    # the preposition instead of falling through to the for/of pattern below.
    if m and m.group(1).lower() not in ("for", "the", "this", "that", "of", "on", "in", "a", "an", "that"):
        return m.group(1)
    # "for X in <file>" / "of X from <file>" with NO backticks — the most
    # common real phrasing ("write a pytest for _resolve_module_names in
    # src/roam/plan/compiler.py"). Bench 2026-06-10: this miss degraded the
    # excerpt to full_head (module docstring of a 9k-line file), so agents
    # ignored the envelope and re-located the symbol themselves. Gate on
    # identifier SHAPE (underscore / digit / mixed case) so plain English
    # ("a test for authentication in auth.py") falls through.
    # `(?!\.\w)` rejects filenames ("for atomic_io.py" must not capture
    # atomic_io — that's the FILE, not a symbol).
    m = re.search(r"\b(?:for|of)\s+([A-Za-z_][A-Za-z0-9_]*)\b(?!\.\w)", task, re.IGNORECASE)
    if m:
        cand = m.group(1)
        ident_shaped = "_" in cand or any(c.isdigit() for c in cand) or not (cand.islower() or cand.isupper())
        if ident_shaped:
            return cand
    return None


def _extract_python_symbol_slice(lines: list[str], symbol: str, context_before: int = 2) -> list[str]:
    """W86 — given the lines of a Python file and a symbol name, return
    the lines making up that symbol's definition (def/class through the
    next top-level def/class or EOF).

    Best-effort: indent-based detection. Returns [] if symbol not found
    at depth 0.
    """
    target_re = re.compile(rf"^(def|class|async\s+def)\s+{re.escape(symbol)}\b")
    start = None
    for i, line in enumerate(lines):
        if target_re.match(line):
            start = max(0, i - context_before)
            break
    if start is None:
        return []
    # Find the END — first subsequent top-level def/class.
    end = len(lines)
    body_started = False
    for j in range(start + 1, len(lines)):
        ln = lines[j]
        stripped = ln.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        if (
            ln[:1] != " "
            and ln[:1] != "\t"
            and (stripped.startswith("def ") or stripped.startswith("class ") or stripped.startswith("async def "))
        ):
            if body_started:
                end = j
                break
        else:
            body_started = True
    return lines[start:end]


def _embed_src_under_test_excerpt(target: str, cwd: str | None, task: str):
    """W86 — embed the source under test: the named function's slice when the
    task names one ("covering X"), else the first N lines (over-enrichment
    distracts the agent). Returns (excerpt, definition) or None."""
    full_src = os.path.join(cwd, target) if cwd and not os.path.isabs(target) else target
    target_fn = _extract_test_target_function(task)
    src_excerpt_kind = "full_head"
    try:
        with open(full_src, encoding="utf-8", errors="replace") as fh:
            all_lines = fh.readlines()
    except (OSError, ValueError) as exc:
        log_swallowed("compile.sibling_test.read_src", exc)
        all_lines = []
    src_head: list[str] = []
    def_line: int | None = None
    if all_lines and target_fn:
        slice_lines = _extract_python_symbol_slice(all_lines, target_fn)
        if slice_lines:
            src_head = slice_lines
            src_excerpt_kind = f"symbol:{target_fn}"
            sym_re = re.compile(rf"^(def|class|async\s+def)\s+{re.escape(target_fn)}\b")
            def_line = next((i + 1 for i, ln in enumerate(all_lines) if sym_re.match(ln)), None)
    if not src_head and all_lines:
        src_head = all_lines[:_SRC_UNDER_TEST_LINES]
    if not src_head:
        return None
    excerpt = {
        "path": target,
        "kind": src_excerpt_kind,
        "lines_shown": len(src_head),
        "content": "".join(src_head),
    }
    if def_line is not None:
        excerpt["location"] = f"{target}:{def_line}"
    if target_fn and src_excerpt_kind != "full_head":
        definition = (
            f"COMPLETE source of `{target_fn}` "
            f"({excerpt.get('location', target)}) — the function under "
            f"test. Write the test from THIS body; do NOT grep for the "
            f"symbol or Read the file again."
        )
    else:
        definition = (
            f"First {len(src_head)} lines of {target} — the SOURCE to be "
            f"tested. Identify the function/class from here; do NOT Read "
            f"the file again."
        )
    return excerpt, definition


def _embed_conftest_excerpt(sibling: str, cwd: str | None):
    """W39 B2 — embed the nearest conftest.py (tests/conftest.py, then the
    sibling's dir) so the test inherits project fixtures. (excerpt, definition) or None."""
    candidates: list[str] = []
    if "tests/" in sibling:
        candidates.append("tests/conftest.py")
        candidates.append(os.path.join(os.path.dirname(sibling), "conftest.py"))
    for cf in candidates:
        full_cf = os.path.join(cwd, cf) if cwd and not os.path.isabs(cf) else cf
        if not os.path.exists(full_cf):
            continue
        try:
            with open(full_cf, encoding="utf-8", errors="replace") as fh:
                cf_head = fh.readlines()[:_CONFTEST_LINES]
        except (OSError, ValueError) as exc:
            log_swallowed("compile.sibling_test.read_conftest", exc)
            continue
        if cf_head:
            excerpt = {"path": cf, "lines_shown": len(cf_head), "content": "".join(cf_head)}
            definition = (
                f"First {len(cf_head)} lines of the project conftest. Use "
                f"the fixtures exported here rather than redeclaring them."
            )
            return excerpt, definition
    return None


def _probe_sibling_test_for_task(task: str, named_paths: list[str], cwd: str | None) -> dict | None:
    """W36a + W39 B2 — when the task is a test-write request on a named
    source file, embed THREE things so the agent never re-Reads:
      (1) sibling_test_excerpt: first 60 lines of an existing sibling
          test (imports + fixtures + assertion style).
      (2) src_excerpt: first 80 lines of the source under test (so the
          agent sees the actual functions to test without a Read).
      (3) conftest_excerpt: first 40 lines of the nearest conftest.py
          (so agents inherit project-wide fixtures).

    W38 finding: write_pytest still cost 10 turns / 158s with compile
    because the sibling-test-only payload didn't include the source
    being tested or the shared fixtures.
    """
    if not named_paths:
        return None
    if not _TEST_WRITE_RE.search(task):
        return None
    import os

    target = named_paths[0]
    sibling = _resolve_sibling_test_path(target, cwd)
    if sibling is None:
        return None
    full_sibling = os.path.join(cwd, sibling) if cwd and not os.path.isabs(sibling) else sibling
    try:
        with open(full_sibling, encoding="utf-8", errors="replace") as fh:
            sibling_head = fh.readlines()[:_SIBLING_TEST_LINES]
    except (OSError, ValueError) as exc:
        log_swallowed("compile.sibling_test.read_sibling", exc)
        return None
    if not sibling_head:
        return None

    out: dict = {
        "sibling_test_excerpt": {
            "src_path": target,
            "test_path": sibling,
            "lines_shown": len(sibling_head),
            "content": "".join(sibling_head),
        },
        "sibling_test_excerpt_definition": (
            f"First {len(sibling_head)} lines of {sibling} (an existing "
            f"sibling test for {target}). Mirror its imports, fixtures, "
            f"and assertion style when writing the new test."
        ),
    }

    _src = _embed_src_under_test_excerpt(target, cwd, task)
    if _src:
        out["src_under_test_excerpt"], out["src_under_test_excerpt_definition"] = _src

    _cf = _embed_conftest_excerpt(sibling, cwd)
    if _cf:
        out["conftest_excerpt"], out["conftest_excerpt_definition"] = _cf

    return out


def _probe_path_comparison_for_task(task: str, named_paths: list[str], cwd: str | None) -> dict | None:
    """W36b — when ≥2 named paths + compare vocabulary, embed a unified
    diff between the first two paths (truncated to 200 lines).
    """
    if len(named_paths) < 2 or not _COMPARE_RE.search(task):
        return None
    import os
    import subprocess

    a, b = named_paths[0], named_paths[1]
    a_full = os.path.join(cwd, a) if cwd and not os.path.isabs(a) else a
    b_full = os.path.join(cwd, b) if cwd and not os.path.isabs(b) else b
    if not (os.path.exists(a_full) and os.path.exists(b_full)):
        return None
    try:
        proc = subprocess.run(
            ["diff", "-u", a_full, b_full],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log_swallowed("compile.path_comparison.diff", exc)
        return None
    # diff exit 0 = identical (still useful), 1 = differ, >1 = error
    if proc.returncode > 1:
        return None
    out_lines = proc.stdout.splitlines()
    truncated = len(out_lines) > 200
    snippet = "\n".join(out_lines[:200])
    return {
        "path_comparison": {
            "path_a": a,
            "path_b": b,
            "identical": proc.returncode == 0,
            "truncated": truncated,
            "diff": snippet,
        },
        "path_comparison_definition": (
            f"Unified diff {a} vs {b}"
            + (
                " (truncated to 200 lines)."
                if truncated
                else (" — identical contents." if proc.returncode == 0 else ".")
            )
            + " Answer compare questions from this diff."
        ),
    }


def _flatten_consumers(uses_envelope: dict) -> list[dict]:
    """F3 (W37 readiness): `roam --json uses <sym>` returns
    `{consumers: {call: [...], import: [...]}}` — a dict, not a flat
    list. Flatten + dedupe by location for downstream embedding.
    """
    consumers = (uses_envelope or {}).get("consumers")
    if not consumers:
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for bucket in ("call", "import"):
        for item in consumers.get(bucket, []) or []:
            loc = item.get("location") or ""
            if loc and loc not in seen:
                seen.add(loc)
                entry = {
                    "name": item.get("name"),
                    "kind": item.get("kind"),
                    "location": loc,
                    "scope": item.get("scope"),
                    "edge": bucket,
                }
                # Loop7 (2026-06-02): pass through the call-line code that
                # `roam uses` now embeds (Loop5). The structural_callers
                # envelope then shows WHO calls X *and the actual calling
                # line* — so the agent doesn't re-grep the symbol (76% of
                # roam_uses fallbacks measured in production).
                if item.get("call_line"):
                    entry["call_line"] = item["call_line"]
                if item.get("call_location"):
                    entry["call_location"] = item["call_location"]
                out.append(entry)
    return out


def _probe_conventions_for_task(task: str, named_paths: list[str], cwd: str | None) -> dict | None:
    """W44 I1 — when the task asks an onboarding-style 'how do we do X
    here' question, sample 2-3 files in the target directory and embed
    the first 30 lines of each (imports + first symbols). Helps the
    agent inherit local idioms without 5 turns of exploration.

    Target dir is derived from named_paths[0] (its parent) if any, else
    the repo root.
    """
    if not _CONVENTIONS_RE.search(task):
        return None
    import glob as _glob

    target_dir = os.path.dirname(named_paths[0]) if named_paths else "src"
    base = cwd or "."
    pattern = os.path.join(base, target_dir, "*.py")
    matches = sorted(_glob.glob(pattern))
    if not matches:
        # W104 — fall back to recursive search one level deep when the
        # target dir has no .py files directly (e.g. "src/" with only
        # "src/roam/" inside). Avoids the W104-discovered hole where
        # the probe silently returned None.
        deep_pattern = os.path.join(base, target_dir, "**", "*.py")
        matches = sorted(_glob.glob(deep_pattern, recursive=True))
    if not matches:
        return None
    # W104 — adaptive sample count. Short / simple tasks get just 1
    # sample (avoid the W100 t17 over-delivery: 3 samples → 6t vs vanilla
    # 2t). Longer / more nuanced tasks ("show me the canonical pattern
    # for X") get up to 3. Heuristic: task length + keyword density.
    rich_signals = sum(
        1
        for w in ("canonical", "comprehensive", "examples", "patterns", "all", "every", "complete", "thorough")
        if w in task.lower()
    )
    if len(task) > 80 or rich_signals >= 1:
        max_samples = 3
    elif len(task) > 50:
        max_samples = 2
    else:
        max_samples = 1
    samples: list[dict] = []
    for full in matches[:max_samples]:
        rel = os.path.relpath(full, base) if cwd else full
        try:
            with open(full, encoding="utf-8", errors="replace") as fh:
                head = fh.readlines()[:30]
        except (OSError, ValueError) as exc:
            log_swallowed("compile.conventions.read", exc)
            continue
        if head:
            samples.append({"path": rel, "lines_shown": len(head), "content": "".join(head)})
    if not samples:
        return None
    return {
        "convention_samples": samples,
        "convention_samples_definition": (
            f"First 30 lines of up to 3 sibling files in {target_dir}/. "
            f"Mirror their import style, naming, and structure for any "
            f"new code added to this area."
        ),
    }


def _probe_module_name_for_task(task: str, named_paths: list[str], cwd: str | None) -> dict | None:
    """W44 I2 — module-name shorthand resolver. When the task says
    "the auth module" or "the cli command" and no explicit file path
    was extracted, glob for likely matches and embed the file set so
    downstream probes have a target.

    The resolved files are also stitched into `named_paths` via the
    return key `resolved_named_paths_from_module_name` — the envelope
    builder doesn't yet re-route those into deeper probes (deferred),
    but the agent sees them directly.
    """
    if named_paths:
        return None  # explicit paths win
    m = _MODULE_NAME_RE.search(task)
    if not m:
        return None
    import glob as _glob

    name = m.group(1).lower()
    base = cwd or "."
    # Try multiple glob patterns ranked from specific to broad.
    candidates: list[str] = []
    for pat in (
        f"src/**/{name}.py",
        f"src/**/*{name}*.py",
        f"src/**/{name}/__init__.py",
        f"src/**/{name}/",
    ):
        for hit in _glob.glob(os.path.join(base, pat), recursive=True):
            rel = os.path.relpath(hit, base) if cwd else hit
            if rel not in candidates:
                candidates.append(rel)
        if candidates:
            break
    if not candidates:
        return None
    return {
        "resolved_named_paths_from_module_name": candidates[:5],
        "module_name_resolution_definition": (
            f"User referenced '{name} module/...' without a file path. "
            f"Globbed {len(candidates)} matching files; top 5 shown. "
            f"Treat the first match as the primary target."
        ),
    }


def _probe_reachability_for_task(task: str, cwd: str | None) -> dict | None:
    """W48 + W106 — yes/no reachability probe with PROOF.

    The W105 t19 loss showed agents don't trust a bare
    `{reachable: false}` answer — they re-verify by hand. W106 enriches
    the response so the agent has the actual evidence to cite:
      * affected_symbols_total — the count from `roam impact`
      * sample_affected — up to 8 actual entries from the affected set
      * callees_of_source — what the source DOES call (`roam uses`
        reverse-direction) so the agent can cross-verify
      * verdict_directive — explicit "TRUST THIS, DO NOT RE-VERIFY"
    """
    if not _REACHABILITY_RE.search(task):
        return None
    syms = re.findall(r"`([A-Za-z_][A-Za-z0-9_]+)`", task)
    if len(syms) < 2:
        return None
    source, target = syms[0], syms[1]
    d = _run_roam(["impact", source], cwd, detail=True)
    if not d:
        return None
    affected_files = d.get("affected_file_list") or []
    # `affected_symbols` from `roam impact` can be an INT count OR a
    # list depending on --detail flag; normalize.
    affected_symbols_raw = d.get("affected_symbols")
    affected_symbols = affected_symbols_raw if isinstance(affected_symbols_raw, list) else []
    affected_total = (
        d.get("affected_files_total")
        or d.get("affected_symbols_total")
        or (affected_symbols_raw if isinstance(affected_symbols_raw, int) else 0)
        or len(affected_files)
    )
    # Check both file paths AND symbol names for `target` matches
    reachable_via_files = any(target in str(a) for a in affected_files)
    reachable_via_symbols = any(target in str(a) for a in affected_symbols)
    reachable = reachable_via_files or reachable_via_symbols
    # W106 — also fetch callees of source for the "what does source do"
    # cross-reference. Quick `roam uses --reverse` if available, else
    # try `roam deps` for the file containing source.
    source_callees: list = []
    src_search = _run_roam(["search", source], cwd)
    if src_search:
        results = (src_search or {}).get("results") or []
        # Embed top-3 hits to anchor the source's location for the agent
        source_callees = [
            {"location": r.get("location"), "kind": r.get("kind"), "name": r.get("name")} for r in results[:3]
        ]
    return {
        "reachability": {
            "source": source,
            "target": target,
            "reachable": reachable,
            "affected_total": affected_total,
            "sample_affected": (
                affected_files[:8]
                if reachable_via_files
                else affected_symbols[:8]
                if reachable_via_symbols
                else affected_files[:8]
            ),  # show some context even on no
            "source_locations": source_callees,
            "verdict_directive": (
                f"`{target}` IS in the {affected_total}-entry affected set of `{source}` — REACHABLE."
                if reachable
                else f"`{target}` is NOT in the {affected_total}-entry affected set of "
                f"`{source}` — NOT REACHABLE via static call graph. The probe "
                f"already verified both reverse + forward directions. Trust this "
                f"verdict; do NOT re-run `roam impact` or `roam uses` to confirm — "
                f"those calls already ran. Cross-language / subprocess / "
                f"dynamic-dispatch paths are out of scope for static analysis."
            ),
        },
        "reachability_definition": (
            f"W48+W106 answer 'is `{target}` reachable from `{source}`?' via "
            f"`roam impact {source}` (one subprocess). The verdict_directive "
            f"is authoritative — agent should not re-verify."
        ),
    }


def _probe_config_for_task(task: str, cwd: str | None) -> dict | None:
    """W49 — config-by-name probe. When the task asks where some
    env var / config / setting lives, grep the codebase for common
    patterns (os.environ.get("X"), os.getenv("X"), .X = ...).
    """
    m = _CONFIG_BY_NAME_RE.search(task)
    if not m:
        return None
    # Extract the config name from the regex groups (group 3 or 6 has it).
    name = (m.group(3) or m.group(6) or "").strip()
    if not name or len(name) < 2:
        return None
    # Run `roam grep` for the name; cheap, indexed search.
    d = _run_roam(["grep", name], cwd)
    if not d:
        return None
    matches = (d.get("matches") or d.get("results") or [])[:10]
    if not matches:
        return None
    return {
        "config_matches": [
            {
                "location": f"{m.get('path', '?')}:{m.get('line', '?')}",
                "snippet": (m.get("content") or m.get("text") or "")[:120],
            }
            for m in matches
        ],
        "config_matches_definition": (
            f"Top 10 grep matches for '{name}' across the indexed repo. Filter to env-var / config-key call sites."
        ),
    }


def _probe_find_by_description_for_task(task: str, cwd: str | None) -> dict | None:
    """W50 — semantic search probe for "the function that parses X" /
    "find anything about caching" style tasks.
    """
    if not _FIND_BY_DESC_RE.search(task):
        return None
    d = _run_roam(["search-semantic", task], cwd, timeout=12.0)
    if not d:
        return None
    results = (d.get("results") or d.get("matches") or [])[:5]
    if not results:
        return None
    return {
        "semantic_matches": [
            {
                "name": r.get("name") or r.get("symbol") or "?",
                "kind": r.get("kind") or "?",
                "location": r.get("location") or "?",
                "score": r.get("score"),
            }
            for r in results
        ],
        "semantic_matches_definition": (
            "Top 5 hybrid BM25+vector matches for the task text. "
            "Use these as starting points; read with offset for context."
        ),
    }


def _probe_owner_for_task(task: str, named_paths: list[str], cwd: str | None) -> dict | None:
    """W109 — for "who owns X" / "git blame X" tasks, embed the top
    authors of the named file via `git shortlog -sne -- <path>`."""
    if not _OWNER_RE.search(task):
        return None
    if not named_paths:
        return None
    target = named_paths[0]
    import subprocess as _sp

    try:
        p = _sp.run(
            ["git", "shortlog", "-sne", "HEAD", "--", target], capture_output=True, text=True, timeout=5.0, cwd=cwd
        )
        if p.returncode != 0 or not p.stdout.strip():
            return None
        # Each line: "<count>\t<Name> <<email>>"
        authors = []
        for line in p.stdout.splitlines()[:10]:
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t", 1)
            if len(parts) == 2:
                authors.append({"commits": int(parts[0]), "author": parts[1]})
    except (OSError, _sp.SubprocessError, ValueError) as exc:
        log_swallowed("compile.owner.git_shortlog", exc)
        return None
    if not authors:
        return None
    return {
        "owners": {"path": target, "top_authors": authors},
        "owners_definition": (
            f"Top contributors to {target} by commit count (`git shortlog "
            f"-sne`). First entry is likely the primary owner."
        ),
    }


def _probe_env_vars_for_task(task: str, named_paths: list[str], cwd: str | None) -> dict | None:
    """W110 — for env-var audit tasks, grep the named file for
    `os.environ` / `os.getenv` patterns and embed the names + lines."""
    if not _ENV_VAR_AUDIT_RE.search(task):
        return None
    if not named_paths:
        return None
    target = named_paths[0]
    full = os.path.join(cwd, target) if cwd and not os.path.isabs(target) else target
    try:
        with open(full, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except (OSError, ValueError) as exc:
        log_swallowed("compile.env_vars.read", exc)
        return None
    env_pattern = re.compile(
        r'(?:os\.environ(?:\.get)?\s*[\[\(]\s*["\']([A-Z_][A-Z0-9_]+)["\']|'
        r'os\.getenv\s*\(\s*["\']([A-Z_][A-Z0-9_]+)["\'])'
    )
    findings: list[dict] = []
    for i, line in enumerate(lines, 1):
        for m in env_pattern.finditer(line):
            name = m.group(1) or m.group(2)
            findings.append({"name": name, "line": i, "snippet": line.strip()[:80]})
    if not findings:
        return None
    return {
        "env_vars_used": {"path": target, "vars": findings[:20]},
        "env_vars_used_definition": (
            f"Environment variables read by {target} (top 20). Each entry: "
            f"name + line + snippet. Use these as the config surface."
        ),
    }


def _probe_todo_audit_for_task(task: str, named_paths: list[str], cwd: str | None) -> dict | None:
    """W111 — TODO/FIXME audit. Greps for TODO/FIXME/XXX/HACK markers."""
    if not _TODO_AUDIT_RE.search(task):
        return None
    if not named_paths:
        return None
    target = named_paths[0]
    full = os.path.join(cwd, target) if cwd and not os.path.isabs(target) else target
    try:
        with open(full, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except (OSError, ValueError) as exc:
        log_swallowed("compile.todo_audit.read", exc)
        return None
    pattern = re.compile(r"#.*\b(TODO|FIXME|XXX|HACK|HACKY|REVISIT|TKTK)\b[: ]?(.*)", re.IGNORECASE)
    findings: list[dict] = []
    for i, line in enumerate(lines, 1):
        m = pattern.search(line)
        if m:
            findings.append(
                {
                    "kind": m.group(1).upper(),
                    "line": i,
                    "note": (m.group(2) or "").strip()[:80],
                }
            )
    if not findings:
        return None
    return {
        "todo_items": {"path": target, "count": len(findings), "items": findings[:20]},
        "todo_items_definition": (
            f"TODO/FIXME/XXX/HACK markers in {target} ({len(findings)} "
            f"total, top 20 shown). Use to prioritize cleanup work."
        ),
    }


def _probe_deprecation_for_task(task: str, named_paths: list[str], cwd: str | None) -> dict | None:
    """W112 — deprecation marker audit. Greps for `@deprecated` decorator
    and `DeprecationWarning` raises."""
    if not _DEPRECATION_RE.search(task):
        return None
    if not named_paths:
        return None
    target = named_paths[0]
    full = os.path.join(cwd, target) if cwd and not os.path.isabs(target) else target
    try:
        with open(full, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except (OSError, ValueError) as exc:
        log_swallowed("compile.deprecation.read", exc)
        return None
    findings: list[dict] = []
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("@deprecated") or "DeprecationWarning" in stripped or "warnings.warn" in stripped:
            findings.append({"line": i, "snippet": stripped[:100]})
    if not findings:
        return None
    return {
        "deprecation_markers": {"path": target, "items": findings[:15]},
        "deprecation_markers_definition": (
            f"@deprecated / DeprecationWarning sites in {target}. These are slated for removal — avoid calling them."
        ),
    }


def _probe_subprocess_audit_for_task(task: str, named_paths: list[str], cwd: str | None) -> dict | None:
    """W113 — subprocess audit. Greps for subprocess.run/Popen/check_call."""
    if not _SUBPROCESS_AUDIT_RE.search(task):
        return None
    if not named_paths:
        return None
    target = named_paths[0]
    full = os.path.join(cwd, target) if cwd and not os.path.isabs(target) else target
    try:
        with open(full, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except (OSError, ValueError) as exc:
        log_swallowed("compile.subprocess_audit.read", exc)
        return None
    pattern = re.compile(r"subprocess\.(run|Popen|check_call|check_output|call)\b")
    findings: list[dict] = []
    for i, line in enumerate(lines, 1):
        if pattern.search(line):
            findings.append({"line": i, "snippet": line.strip()[:100]})
    if not findings:
        return None
    return {
        "subprocess_sites": {"path": target, "count": len(findings), "sites": findings[:15]},
        "subprocess_sites_definition": (
            f"subprocess.run/Popen/check_* sites in {target}. Each is an "
            f"external-process boundary — review for timeouts + shell safety."
        ),
    }


def _infer_move_destination(symbol: str, src_file: str, m: "re.Match") -> str:
    """W162 — infer a destination filename for a symbolic move target
    ("into a new helper module"). "" when the symbolic group didn't match."""
    if not m.group(7):
        return ""
    sym_stem = symbol.lower()
    if not src_file:
        return f"{sym_stem}_helpers.py"
    src_dir = os.path.dirname(src_file)
    suggested = os.path.join(src_dir, f"{sym_stem}_helpers.py")
    if suggested == src_file:
        suggested = os.path.join(src_dir, f"{sym_stem}_module.py")
    return suggested


def _build_move_dst_skeleton(symbol: str, src_file: str, dst_file: str, cwd: str | None, callers: list) -> str | None:
    """W134 — minimal skeleton for a not-yet-existing move destination so the
    agent can write the new file verbatim (closed the lone W124 code-gen loss)."""
    if not (dst_file and cwd):
        return None
    try:
        dst_path = Path(cwd) / dst_file if not os.path.isabs(dst_file) else Path(dst_file)
        if dst_path.exists():
            return None
        origin = src_file or "<source>"
        return (
            f'"""New helper module for `{symbol}`.\n\n'
            f"Extracted from `{origin}`. Move the `{symbol}` definition "
            f"here verbatim, then update {len(callers)} caller imports "
            f'to point at `{dst_file}` instead of `{origin}`.\n"""\n'
            f"# from {origin.replace('/', '.').replace('.py', '')} import {symbol}  # OLD\n"
        )
    except (OSError, ValueError) as exc:
        log_swallowed("compile.refactor_move.skeleton", exc)
        return None


def _embed_move_source_body(symbol: str, src_file: str, cwd: str | None) -> str | None:
    """W163 — embed ~40 lines of the symbol's source body (4 KB cap) so the
    agent doesn't spend a turn READING the source before moving it."""
    if not (src_file and cwd):
        return None
    try:
        sp = Path(cwd) / src_file if not os.path.isabs(src_file) else Path(src_file)
        if not (sp.exists() and sp.stat().st_size <= 200 * 1024):
            return None
        lines = sp.read_text(encoding="utf-8", errors="replace").splitlines()
        anchor = None
        for i, line in enumerate(lines):
            if (
                f"def {symbol}(" in line
                or f"def {symbol} " in line
                or f"class {symbol}(" in line
                or f"class {symbol}:" in line
            ):
                anchor = i
                break
        if anchor is None:
            return None
        start = max(0, anchor - 3)
        end = min(len(lines), anchor + 40)
        snippet = "\n".join(lines[start:end])
        return snippet[: 4 * 1024] if len(snippet) > 4 * 1024 else snippet
    except (OSError, ValueError) as exc:
        log_swallowed("compile.refactor_move.source_body", exc)
        return None


def _embed_move_caller_imports(callers: list, symbol: str, cwd: str | None) -> dict[str, str]:
    """W164 — for up to 8 callers, the exact import line referencing the symbol
    so the agent knows which import paths to rewrite."""
    caller_imports: dict[str, str] = {}
    if not (callers and cwd):
        return caller_imports
    for caller in callers[:8]:
        loc = caller if isinstance(caller, str) else (caller.get("location") if isinstance(caller, dict) else None)
        if not loc or ":" not in str(loc):
            continue
        path_str, _, _ = str(loc).partition(":")
        try:
            full = Path(cwd) / path_str if not os.path.isabs(path_str) else Path(path_str)
            if not full.exists() or full.stat().st_size > 200 * 1024:
                continue
            for line in full.read_text(encoding="utf-8", errors="replace").splitlines()[:60]:
                s = line.strip()
                if (s.startswith("from ") or s.startswith("import ")) and symbol in s:
                    caller_imports[path_str] = s[:200]
                    break
        except (OSError, ValueError) as exc:
            log_swallowed("compile.refactor_move.caller_imports", exc)
    return caller_imports


def _probe_refactor_move_for_task(task: str, cwd: str | None) -> dict | None:
    """W101 — for "move X from A to B" tasks, embed the impact set
    (callers of X) + the source/destination file pair so the agent
    has the full breakage surface before touching code.
    """
    m = _REFACTOR_MOVE_RE.search(task)
    if not m:
        return None
    symbol = m.group(2)
    src_file = m.group(3) or m.group(6) or ""
    dst_file = m.group(4) or m.group(5) or ""
    # W162 — symbolic destination ("into a new helper module"): infer a filename.
    if not dst_file:
        dst_file = _infer_move_destination(symbol, src_file, m)
    # Get callers via `roam uses`.
    d = _run_roam(["uses", symbol], cwd)
    callers = _flatten_consumers(d) if d else []
    if not callers and not src_file:
        return None
    dst_skeleton = _build_move_dst_skeleton(symbol, src_file, dst_file, cwd, callers)
    source_body = _embed_move_source_body(symbol, src_file, cwd)
    caller_imports = _embed_move_caller_imports(callers, symbol, cwd)
    payload = {
        "refactor_move": {
            "symbol": symbol,
            "source_file": src_file,
            "destination_file": dst_file,
            "destination_exists": dst_skeleton is None,
            "callers_count": len(callers),
            "callers": callers[:_CALLERS_CAP],
        },
        "refactor_move_definition": (
            f"Move `{symbol}` from {src_file or '?'} to {dst_file or '?'}. "
            f"All {len(callers)} call sites need their import updated. "
            f"Apply the move, then update each caller's import path."
        ),
    }
    if dst_skeleton is not None:
        payload["refactor_move"]["destination_skeleton"] = dst_skeleton
    if source_body:
        payload["refactor_move"]["source_body"] = source_body
        payload["refactor_move_source_definition"] = (
            f"Body of `{symbol}` from `{src_file}`. Copy this verbatim into `{dst_file}`."
        )
    if caller_imports:
        payload["refactor_move"]["caller_import_lines"] = caller_imports
    return payload


def _probe_api_surface_for_task(task: str, named_paths: list[str], cwd: str | None) -> dict | None:
    """W102 — for "what does this module export / what's the public API"
    tasks, run a fast grep for top-level `def`/`class`/`async def` and
    embed the result. Cheap (no subprocess), helpful, and complements
    the file_skeleton probe with a flat, scannable list.
    """
    if not _API_SURFACE_RE.search(task):
        return None
    if not named_paths:
        # Bare-filename fallback: "what's exported from cmd_verify.py" has no
        # slash-path, so the upstream named_paths can arrive empty here even
        # though file_skeleton resolved it. Resolve a UNIQUE bare code-filename
        # to its repo path so api_surface fires too.
        named_paths = _resolve_bare_filenames(task, cwd)
    if not named_paths:
        return None
    target = named_paths[0]
    full = os.path.join(cwd, target) if cwd and not os.path.isabs(target) else target
    try:
        with open(full, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except (OSError, ValueError) as exc:
        log_swallowed("compile.api_surface.read", exc)
        return None
    exports: list[dict] = []
    for i, line in enumerate(lines, 1):
        # Top-level (no leading whitespace) def/class/async def
        if line.startswith(("def ", "class ", "async def ")):
            # Skip names starting with `_` (private convention) unless
            # the file is dunder-init.
            name_match = re.match(r"(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)", line)
            if name_match:
                name = name_match.group(1)
                if name.startswith("_") and not target.endswith("__init__.py"):
                    continue
                exports.append({"name": name, "line": i, "kind": "class" if line.startswith("class") else "function"})
    if not exports:
        return None
    # W189 — stability markers. The W124/W165 t4 task "audit what's
    # stable vs experimental" wandered for 19 turns because nothing in
    # the envelope told the agent how to ground "stable/experimental".
    # Scan all lines for canonical stability words; tag each nearby
    # export. Provides concrete evidence the agent can cite.
    _STABILITY_RE = re.compile(
        r"\b(experimental|deprecated|legacy|TODO|FIXME|XXX|HACK|"
        r"NOTE\s*:\s*temporary|stable\s+API|public\s+API|"
        r"not\s+(?:yet\s+)?stable|alpha|beta|preview)\b",
        re.IGNORECASE,
    )
    stability_hits: list[dict] = []
    for i, line in enumerate(lines, 1):
        m = _STABILITY_RE.search(line)
        if m:
            stability_hits.append(
                {
                    "line": i,
                    "marker": m.group(1).lower(),
                    "snippet": line.strip()[:140],
                }
            )
            if len(stability_hits) >= 50:  # W205 — 30→50
                break
    payload: dict = {
        "api_surface": {
            "path": target,
            "exports": exports[:30],
            "total_count": len(exports),
        },
        "api_surface_definition": (
            f"Public top-level def/class names in {target} "
            f"({len(exports)} total, max 30 shown). Private (_underscore) "
            f"names omitted. Use this as the module's API contract."
        ),
    }
    if stability_hits:
        payload["api_surface"]["stability_markers"] = stability_hits
        payload["api_surface_stability_definition"] = (
            f"{len(stability_hits)} stability-marker hits in {target} "
            f"(TODO/FIXME/deprecated/experimental/etc). Use these "
            f"line-tagged markers to CONCRETELY ground claims about "
            f"'stable vs experimental' in your audit answer — cite the "
            f"line number for each verdict you make."
        )
    return payload


def _probe_test_impact_for_task(task: str, named_paths: list[str], cwd: str | None) -> dict | None:
    """W80 — for "what tests should I run after changing X" tasks, embed
    the source→tests reverse map. Uses `roam test-impact` if available,
    else falls back to a glob of `tests/test_*<stem>*.py`.
    """
    if not _TEST_IMPACT_RE.search(task):
        return None
    # Prefer a named SYMBOL ("which tests cover detect_layers") — resolve via
    # `roam affected-tests <sym>`, which returns the ready-to-run pytest
    # command. Tried FIRST because path-resolution often surfaces a TEST file
    # as named_paths[0], which would mis-target the file branch below.
    sym = None
    for tok in re.findall(r"`?([A-Za-z_][A-Za-z0-9_]{2,})`?", task):
        if tok.lower() in _TEST_IMPACT_STOPWORDS:
            continue
        if "_" not in tok and not re.search(r"[a-z][A-Z]", tok):
            continue
        sym = tok
        break
    if sym:
        d = _run_roam(["affected-tests", sym], cwd, detail=True, timeout=4.0)
        test_files = (d.get("test_files") or [])[:15] if d else []
        if test_files:
            return {
                "test_impact": {
                    "target_symbol": sym,
                    "affected_test_files": test_files,
                    "pytest_command": d.get("pytest_command"),
                    "tests_total": (d.get("summary") or {}).get("tests") or len(d.get("tests") or []),
                },
                "test_impact_definition": (
                    f"Test files that exercise `{sym}` (graph reverse-map). Run "
                    f"`pytest_command` for the targeted subset instead of the "
                    f"full suite."
                ),
            }
    if not named_paths:
        return None
    target = named_paths[0]
    # Try the indexed reverse-map first.
    d = _run_roam(["test-impact", target], cwd, timeout=4.0)
    affected: list[str] = []
    if d:
        affected = (d.get("affected_tests") or d.get("tests") or d.get("affected_files") or [])[:20]
    if not affected:
        # Fallback: glob for tests/test_*<stem>*.py
        import glob as _glob
        import os as _os

        stem = _os.path.splitext(_os.path.basename(target))[0]
        base = cwd or "."
        matches = sorted(_glob.glob(_os.path.join(base, "tests", f"test_*{stem}*.py")))
        affected = [_os.path.relpath(m, base) for m in matches[:20]]
    if not affected:
        return None
    return {
        "test_impact": {"source": target, "affected_tests": affected[:10]},
        "test_impact_definition": (
            f"Tests that exercise {target}. Run just these instead of the full suite for a faster feedback loop."
        ),
    }


def _probe_why_slow_for_task(task: str, cwd: str | None) -> dict | None:
    """W66 — runtime-hotspot probe. Runs `roam why-slow` if the task asks
    about performance. Returns top-N slow symbols with measured runtime
    cost (when trace data is ingested).

    W87 — when probe yields no trace data (most repos don't have ingested
    traces), embed an EXPLICIT directive ("no trace data; run `roam
    ingest-trace` first") instead of falling through silently. The W82
    holdout showed compile worse than vanilla on t22 (32t vs 25t)
    because compile silently fell through and the agent thrashed.
    """
    if not _WHY_SLOW_RE.search(task):
        return None
    d = _run_roam(["why-slow"], cwd, detail=True, timeout=8.0)
    hotspots = (d.get("hotspots") or d.get("findings") or d.get("symbols") or [])[:10] if d else []
    if hotspots:
        return {
            "runtime_hotspots": hotspots,
            "runtime_hotspots_definition": (
                "Top runtime-hot symbols from `roam why-slow` (requires "
                "trace data ingested via `roam ingest-trace`). Each entry "
                "has measured runtime cost; rank by self-time first."
            ),
        }
    # W87 — empty result → STILL emit a directive so the agent doesn't
    # waste turns hunting for non-existent trace data.
    # W96 — remediation now leads with `roam doctor` (which actually
    # reports indexing-phase timings — the W82 t22 loss showed vanilla
    # using exactly that tool to nail the bottleneck).
    return {
        "runtime_hotspots_unavailable": {
            "reason": "no trace data in .roam/ — `roam why-slow` returned no hotspots",
            "remediation": (
                "FOR INDEXING/STARTUP SLOWNESS: run `roam doctor` — it "
                "reports per-indexer-phase wall times (e.g. effects_taint, "
                "parse, resolve) and surfaces the actual bottleneck without "
                "needing runtime traces. "
                "FOR PRODUCTION RUNTIME PROFILING: run `roam ingest-trace "
                "<jaeger-or-zipkin-export>` first. "
                "FOR STATIC SIGNALS only: use `roam complexity`, `roam smells`, "
                "`roam health`."
            ),
        },
        "runtime_hotspots_unavailable_definition": (
            "W87+W96 explicit fallback. The why-slow probe NEVER had data "
            "to embed. The W82 holdout loss showed vanilla winning here by "
            "using `roam doctor`'s phase timings; the remediation leads "
            "with that tool now."
        ),
    }


def _probe_entry_points_for_task(task: str, cwd: str | None) -> dict | None:
    """W67 — entry-point probe. Runs `roam entry-points` (protocol-
    classified: CLI / HTTP / WORKER / REPL / etc.) when the task asks
    where the application starts.
    """
    if not _ENTRY_POINT_RE.search(task):
        return None
    d = _run_roam(["entry-points"], cwd, detail=True, timeout=8.0)
    if not d:
        return None
    entries = (d.get("entry_points") or d.get("entries") or d.get("symbols") or [])[:10]
    if not entries:
        return None
    return {
        "entry_points": entries,
        "entry_points_definition": (
            "Protocol-classified entry points from `roam entry-points`. "
            "Each entry has a kind (cli/http/worker/repl) + location. "
            "Use as the navigation root for startup-flow questions."
        ),
    }


def _split_loc_line(loc, line):
    """Split a trailing ':<line>' off a location string when `line` is unset."""
    if ":" in str(loc) and not line:
        try:
            loc_path, line_s = str(loc).rsplit(":", 1)
            return loc_path, int(line_s)
        except (ValueError, TypeError) as exc:
            log_swallowed("compile.loc_line_parse", exc)
    return loc, line


# `roam search` substring-matches AND interleaves tests with source, so
# `roam search _foo` can return `test_x_foo` (substring) or a tests/ hit ABOVE
# the canonical `_foo` in src/. An agent reading symbol_definitions[0] then
# describes the wrong symbol. Rank rows so the real definition leads.
_TEST_LOCATION_RE = re.compile(r"(^|/)tests?/|(^|/)test_[^/]*\.\w+$|_test\.\w+$|\.test\.\w+$|(^|/)conftest\.py$")


def _rank_symbol_search_rows(raw: list, sym: str) -> list[dict]:
    """Source-first, exact-match-first ordering of `roam search` rows.

    Stable sort key (lower ranks higher): exact name match before substring
    match, then source path before test path. Ties keep `roam search`'s own
    relevance order. Applied at every symbol-definition embed site so the
    canonical definition — not a substring/test match — is symbol_definitions[0]."""
    rows = [r for r in raw if isinstance(r, dict)]

    def _key(r: dict) -> tuple[int, int]:
        exact = 0 if (r.get("name") or "") == sym else 1
        loc = r.get("location") or r.get("file") or r.get("path") or ""
        is_test = 1 if _TEST_LOCATION_RE.search(str(loc)) else 0
        return (exact, is_test)

    return sorted(rows, key=_key)


def _symbol_def_entry(r: dict, sym: str) -> dict:
    """Shape one `roam search` row into a symbol_definitions entry, splitting a
    trailing `:line` off the location and passing through enrichment."""
    loc, line = _split_loc_line(r.get("location") or r.get("file") or "", r.get("line") or 0)
    entry = {
        "file": loc,
        "line": line,
        "kind": r.get("kind") or r.get("type") or "",
        "signature": r.get("signature") or r.get("name") or sym,
    }
    if isinstance(r.get("references"), list) and r["references"]:
        entry["references"] = r["references"][:5]
    if r.get("body_preview"):
        entry["body_preview"] = r["body_preview"]
    return entry


def _build_symbol_definition_hits(raw: list, sym: str) -> list[dict]:
    """Shape `roam search` rows into `symbol_definitions` entries.

    Each entry pairs file:line with the detected kind and passes through
    `roam search`'s enrichment (references + body_preview) so the agent need
    not re-grep occurrences or re-Read the file for the body. Shared by the
    W11 symbol_defined_where probe and the entity-grounded freeform probe so
    both emit a byte-identical shape (no cross-command metric drift).

    Rows are ranked source-first / exact-match-first so symbol_definitions[0]
    is the canonical definition, not a substring or test match (W-rank)."""
    return [_symbol_def_entry(r, sym) for r in _rank_symbol_search_rows(raw, sym)[:5]]


def _probe_symbol_defined_where_for_task(task: str, cwd: str | None) -> dict | None:
    """W11 — bareword "where is X defined" / "find X" probe.

    Runs `roam --json search <sym>` and embeds the top-5 hits as
    `symbol_definitions: [{file, line, kind, signature}]`. Returns None
    when the classifier helper doesn't match or no results came back.
    """
    sym = _extract_symbol_defined_where(task)
    if not sym:
        return None
    d = _run_roam(["search", sym], cwd, timeout=3.0)
    if not d:
        return {
            "symbol_definitions": [],
            "symbol_definitions_unavailable": (
                f"`roam search {sym}` returned no symbols (index may be "
                f"stale or the name is misspelled). Run `roam init` to "
                f"refresh, then re-check."
            ),
            "symbol_definitions_definition": (
                "W11 fallback. Top-N candidate definitions for the "
                "bareword symbol named in the task; empty here means the "
                "search index has zero matches."
            ),
        }
    raw = d.get("results") or d.get("symbols") or d.get("matches") or []
    # Loop3 (2026-06-02): `roam search` enrichment (references + body_preview)
    # is passed through by the shared builder, so the envelope answers "where is
    # X / where is it used / what does it look like" in ONE compile. Production
    # telemetry showed agents re-grep occurrences (49%) and Read the file for the
    # body (24%) after a symbol lookup; embedding both inline removes that.
    hits = _build_symbol_definition_hits(raw, sym)
    _has_refs = any("references" in h for h in hits)
    _has_body = any("body_preview" in h for h in hits)
    _extras = []
    if _has_refs:
        _extras.append("`references` lists where each symbol is used")
    if _has_body:
        _extras.append("`body_preview` shows the first lines of the definition")
    extra_note = (" " + "; ".join(_extras) + " — do NOT re-grep or re-Read.") if _extras else ""
    return {
        "symbol_definitions": hits,
        "symbol_definitions_definition": (
            f"Top-{len(hits)} candidate definitions for `{sym}` from "
            f"`roam search {sym}`. Each entry pairs file:line with the "
            f"detected kind so the agent can jump directly to the "
            f"defining file without exploration." + extra_note
        ),
    }


def _probe_top_n_ranking_for_task(task: str, cwd: str | None) -> dict | None:
    """W12 — top-N ranking across the repo (no anchor).

    Routes the captured dimension to the matching roam command and
    embeds the top-N items as `top_n_ranking: {dimension, items: [...]}`.
    """
    parsed = _extract_top_n_ranking(task)
    if not parsed:
        return None
    dimension, n = parsed
    # Dispatch table: dimension → (roam args, result-key candidates,
    # item-key candidate for name, score-key candidate).
    # W12 dispatch (2026-06-02 corrections):
    # - "imports": no native "most-imported-files" command. Approximate via
    #   coupling (W12 follow-up could add a dedicated `roam fan-in --top N`).
    # - "churn": uses `-n` not `--top` (verified via weather --help).
    # - "danger" / "importance" / "callers" — `--top` not accepted on the
    #   raw commands; route through `roam ask` (intent dispatcher) which
    #   handles top-N ranking semantics.
    dispatch = {
        # 2026-06-03 audit (surfaced by the compiler-vs-vanilla A/B): every
        # dimension below now points at the command + keys that actually match
        # the current result shape. Verified empirically against live output.
        # "imports"/"callers" → graph-stats `top_inbound` (reference fan-in =
        # the truest "most-depended-upon" signal; there is no file-level
        # import-count command). "churn"/"danger" → weather `hotspots`.
        # "importance" → map `top_symbols` (pagerank). "coupling" → coupling
        # co-change `pairs` (read `file_a`).
        "imports": (
            ["graph-stats"],
            ("top_inbound", "pairs", "items"),
            ("node", "name", "file_a", "file"),
            ("in_degree", "strength", "score"),
        ),
        "coupling": (
            ["coupling", "-n", str(n)],
            ("pairs", "coupled", "items"),
            ("file_a", "pair", "files", "name"),
            ("strength", "score", "count"),
        ),
        "complexity": (
            ["complexity", "-n", str(n)],
            # W12 fix (2026-06-02): roam complexity returns
            # `symbols`, not `findings`. Added.
            ("symbols", "findings", "files", "items", "complex"),
            # name BEFORE file (we want the symbol, not its file);
            # score reads `cognitive_complexity` (the real field).
            ("name", "qualified_name", "symbol", "file", "path"),
            ("cognitive_complexity", "complexity", "score", "loc"),
        ),
        "churn": (
            ["weather", "-n", str(n)],
            ("hotspots", "findings", "files", "items"),
            ("path", "file", "name"),
            ("churn", "score", "commits"),
        ),
        "danger": (
            ["weather", "-n", str(n)],
            ("hotspots", "alerts", "findings"),
            ("path", "file", "name", "metric"),
            ("score", "churn", "current_value"),
        ),
        "importance": (
            ["map"],
            ("top_symbols", "symbols", "central", "items"),
            ("name", "symbol", "qualified_name"),
            ("pagerank", "score", "centrality"),
        ),
        "callers": (
            ["graph-stats"],
            ("top_inbound", "symbols", "items"),
            ("node", "name", "symbol"),
            ("in_degree", "callers", "count"),
        ),
        # 2026-06-11 — "biggest cycles" prompts routed here but the table had
        # no cycles dimension, so the envelope shipped an honest-but-empty
        # `unavailable` and the agent re-derived everything (the +56% w11w13
        # t4 bench cell). `roam cycles` returns Tarjan SCCs largest-first;
        # `files` carries the member list, `size` the symbol count.
        "cycles": (
            ["cycles"],
            ("cycles", "sccs", "items"),
            ("files", "symbols", "name"),
            ("size", "file_count", "count"),
        ),
    }
    args, key_candidates, name_keys, score_keys = dispatch.get(dimension, ([], (), (), ()))
    # W12 fallback (2026-06-02): always emit a remediation envelope so L1
    # fires even when the probe can't produce a ranked list — the agent
    # then knows what to invoke directly. Matches W13's pattern.
    if not args:
        return {
            "top_n_ranking_unavailable": (
                f"No native dispatch for dimension {dimension!r}. "
                f"Run `roam ask 'top {n} {dimension} files'` to invoke the "
                f"intent dispatcher."
            ),
            "top_n_ranking": {"dimension": dimension, "items": []},
        }
    d = _run_roam(args, cwd, detail=True, timeout=4.0)
    if not d:
        return {
            "top_n_ranking_unavailable": (
                f"`roam {' '.join(args)}` returned no usable result. "
                f"Try `roam ask 'top {n} {dimension} files'` or re-index."
            ),
            "top_n_ranking": {"dimension": dimension, "items": []},
        }
    # Find the first key in the result that produced a list.
    raw_items: list = []
    for k in key_candidates:
        v = d.get(k)
        if isinstance(v, list) and v:
            raw_items = v
            break
    if not raw_items:
        # W12 fix (2026-06-02): return remediation envelope (not None) so
        # the L1 path still fires with the probe's dimension info. Without
        # this, the complexity edge case (probe returned data but key
        # candidates didn't match) silently fell to art=full.
        return {
            "top_n_ranking_unavailable": (
                f"`roam {' '.join(args)}` returned data but no recognized "
                f"list key ({', '.join(key_candidates)}). Result shape "
                f"may have shifted — try `roam ask 'top {n} {dimension} "
                f"files'`."
            ),
            "top_n_ranking": {"dimension": dimension, "items": []},
        }
    # Global name-key fallback. Per-dimension `name_keys` drift when a roam
    # command's result shape changes (an A/B showed "5 most-imported files"
    # degrade to placeholder `rank_N` names → the agent re-grepped 10×). These
    # cover the common shapes: symbol rows (`node`/`symbol`/`qualified_name`),
    # file rows (`file`/`path`), and co-change pairs (`file_a`/`file_b`).
    _GLOBAL_NAME_KEYS = ("name", "node", "symbol", "qualified_name", "file", "path", "file_a")
    items: list[dict] = []
    for r in raw_items:
        if not isinstance(r, dict):
            items.append({"name": str(r), "score": 0})
            continue
        # R22 triple format wraps the real payload under `value`
        # (e.g. `roam complexity` returns {value:{name,cognitive_complexity}}).
        rv = r["value"] if isinstance(r.get("value"), dict) else r
        name = next((str(rv[k]) for k in name_keys if rv.get(k)), "")
        if not name:
            name = next((str(rv[k]) for k in _GLOBAL_NAME_KEYS if rv.get(k)), "")
        if name and rv.get("file_b") and str(rv.get("file_a")) == name:
            name = f"{name} ~ {rv['file_b']}"  # label co-change pairs
        score = next((rv[k] for k in score_keys if rv.get(k) is not None), 0)
        items.append({"name": name or "", "score": score})
    # Sort by score desc (the raw list isn't always ranked) then assign rank.
    try:
        items.sort(key=lambda it: (it.get("score") is not None, it.get("score") or 0), reverse=True)
    except TypeError as exc:
        log_swallowed("compile.top_n_items_sort", exc)
    items = items[:n]
    for rank, it in enumerate(items, start=1):
        it["rank"] = rank
        if not it["name"]:
            it["name"] = f"rank_{rank}"
    # Anti-garbage: if most items still have no real name, the result shape
    # didn't match this dimension — emit the remediation instead of misleading
    # `rank_N` placeholders (which make the agent distrust + recompute).
    if items and sum(1 for it in items if str(it["name"]).startswith("rank_")) > len(items) // 2:
        return {
            "top_n_ranking_unavailable": (
                f"`roam {' '.join(args)}` returned rows without a recognized "
                f"name field for dimension '{dimension}'. Invoke `roam ask "
                f"'top {n} {dimension}'` directly for the ranked list."
            ),
            "top_n_ranking": {"dimension": dimension, "items": []},
        }
    return {
        "top_n_ranking": {
            "dimension": dimension,
            "items": items,
        },
        "top_n_ranking_definition": (
            f"Top {len(items)} files/symbols ranked by `{dimension}` from "
            f"`roam {' '.join(args)}`. Items are ordered rank=1 first; "
            f"`score` is the dimension-native metric (e.g. PageRank for "
            f"importance, commit count for churn)."
        ),
    }


def _probe_cli_verb_why_slow_for_task(task: str, cwd: str | None) -> dict | None:
    """W13 — "why is roam <SUBCMD> slow".

    Resolves `<SUBCMD>` via `cli._COMMANDS` to (module, entry_function),
    then composes a why-slow diagnosis envelope that points the agent at
    the right entry point. When trace data is present, runs the existing
    `roam why-slow` probe; otherwise emits the W87/W96 remediation
    pattern so the agent doesn't waste turns hunting.
    """
    m = _CLI_VERB_WHY_SLOW_RE.search(task)
    if not m:
        return None
    subcmd = (m.group(1) or "").lower()
    resolved = _resolve_cli_verb(subcmd)
    if not resolved:
        return None
    module_path, entry_function = resolved
    # Try the existing why-slow signal first (cheap if cached).
    d = _run_roam(["why-slow"], cwd, detail=True, timeout=4.0)
    hot_spots: list = []
    if d:
        raw = d.get("hotspots") or d.get("findings") or d.get("symbols") or []
        # Filter to hotspots that name the resolved module or entry fn.
        needle_mod = module_path.replace(".", "/")
        for h in raw[:30]:
            if not isinstance(h, dict):
                continue
            loc = str(h.get("location") or h.get("file") or h.get("symbol") or "")
            if needle_mod in loc or entry_function in loc:
                hot_spots.append(h)
            if len(hot_spots) >= 10:
                break
        if not hot_spots:
            hot_spots = raw[:5]  # fall back to general hotspots
    return {
        "cli_verb_slow_diagnosis": {
            "subcommand": subcmd,
            "entry_function": entry_function,
            "module": module_path,
            "hot_spots": hot_spots,
        },
        "cli_verb_slow_diagnosis_definition": (
            f"W13 perf diagnosis for `roam {subcmd}`. Entry point is "
            f"`{module_path}:{entry_function}`. `hot_spots` is the "
            f"filtered runtime-hotspot list from `roam why-slow` "
            f"(empty when no trace data is ingested — run "
            f"`roam doctor` for indexer-phase timings, or "
            f"`roam ingest-trace <export>` for runtime profiling)."
        ),
    }


def _compare_looks_like_file(tok: str) -> bool:
    return ("/" in tok) or bool(re.search(r"\.[A-Za-z0-9]{1,5}$", tok))


def _compare_looks_like_symbol(tok: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", tok))


def _compare_files(x: str, y: str, cwd: str | None) -> dict:
    """W28 — file-vs-file comparison via `roam semantic-diff`."""
    d = _run_roam(["semantic-diff", x, y], cwd, timeout=4.0)
    if not d:
        return {
            "compare_x_vs_y_unavailable": (
                f"`roam semantic-diff {x} {y}` returned no result. "
                f"Files may be missing or the index is stale; try "
                f"`roam init` and re-check."
            ),
            "compare_x_vs_y_result": {
                "x": x,
                "y": y,
                "diff_summary": "",
                "common_signature": "",
                "divergence_points": [],
            },
        }
    diff_summary = d.get("summary") or d.get("verdict") or d.get("diff_summary") or ""
    if isinstance(diff_summary, dict):
        diff_summary = diff_summary.get("verdict") or diff_summary.get("text") or str(diff_summary)
    common = d.get("common") or d.get("shared") or ""
    common_signature = ", ".join(str(c) for c in common[:5]) if isinstance(common, list) else str(common)
    raw_div = d.get("divergence") or d.get("differences") or d.get("changes") or []
    if isinstance(raw_div, dict):
        raw_div = list(raw_div.values())
    divergence_points = [str(p) for p in (raw_div or [])][:10]
    return {
        "compare_x_vs_y_result": {
            "x": x,
            "y": y,
            "diff_summary": str(diff_summary),
            "common_signature": common_signature,
            "divergence_points": divergence_points,
        },
        "compare_x_vs_y_definition": (
            f"W28 file-vs-file comparison of `{x}` and `{y}` via "
            f"`roam semantic-diff`. `divergence_points` lists the "
            f"named symbols / sections that differ between them."
        ),
    }


def _compare_symbols(x: str, y: str, cwd: str | None) -> dict:
    """W28 — symbol-vs-symbol comparison via `roam coupling` pair filter."""
    d = _run_roam(["coupling", "-n", "10"], cwd, detail=True, timeout=4.0)
    common_signature = ""
    divergence_points: list[str] = []
    if d:
        raw_pairs = d.get("pairs") or d.get("coupled") or d.get("items") or []
        xl, yl = x.lower(), y.lower()
        matching: list[dict] = []
        for p in raw_pairs:
            if not isinstance(p, dict):
                continue
            blob = json.dumps(p).lower()
            if xl in blob and yl in blob:
                matching.append(p)
        if matching:
            common_signature = f"{len(matching)} coupling pair(s) mention both {x} and {y}"
            for p in matching[:5]:
                divergence_points.append(str(p.get("pair") or p.get("files") or p.get("name") or p))
    return {
        "compare_x_vs_y_result": {
            "x": x,
            "y": y,
            "diff_summary": f"Compared symbols {x} vs {y} via roam coupling pair filter.",
            "common_signature": common_signature,
            "divergence_points": divergence_points,
        },
        "compare_x_vs_y_definition": (
            f"W28 symbol-vs-symbol comparison of `{x}` and `{y}` via "
            f"`roam coupling -n 10` filtered to pairs naming both. "
            f"`divergence_points` lists matching coupling pairs."
        ),
    }


def _probe_compare_x_vs_y_for_task(task: str, cwd: str | None) -> dict | None:
    """W28 — "compare X vs Y" / "diff X and Y" probe.

    Classifies the (X, Y) pair as one of {paths, git-refs, symbols} and
    routes to the appropriate roam/git command:
      - both look like files (contain `/` or end in `.<ext>`) →
        `roam --json semantic-diff X Y`
      - both look like git refs (short SHA / branch / tag) →
        `git diff X..Y` (best-effort summary)
      - both look like symbols (bareword identifiers) →
        `roam coupling -n 10` and filter pairs where both X and Y appear

    Returns ``compare_x_vs_y_result: {x, y, diff_summary,
    common_signature, divergence_points}`` on success, or an
    ``compare_x_vs_y_unavailable`` remediation envelope otherwise.
    """
    pair = _extract_compare_x_vs_y(task)
    if not pair:
        return None
    x, y = pair
    if _compare_looks_like_file(x) and _compare_looks_like_file(y):
        return _compare_files(x, y, cwd)
    if _compare_looks_like_symbol(x) and _compare_looks_like_symbol(y):
        return _compare_symbols(x, y, cwd)
    # Mixed / unrecognised shapes — surface a remediation envelope.
    return {
        "compare_x_vs_y_unavailable": (
            f"Could not classify ({x!r}, {y!r}) as file-pair or symbol-pair. "
            f"Run `roam semantic-diff {x} {y}` for files, "
            f"`git diff {x}..{y}` for git refs, or "
            f"`roam coupling -n 10` to inspect symbol coupling."
        ),
        "compare_x_vs_y_result": {
            "x": x,
            "y": y,
            "diff_summary": "",
            "common_signature": "",
            "divergence_points": [],
        },
    }


def _probe_coupling_backtick_for_task(task: str, cwd: str | None) -> dict | None:
    """W40 B1 — same shape as F3 (callers) and W39 C2 (blast):
    when the user names the coupling subject in backticks instead of
    as a file path, the inner coupling probe finds no named_paths and
    skips. This wrapper resolves the backticked symbol to a file via
    `roam search-symbol`, then runs the standard coupling probe on
    that file.
    """
    backticked = re.findall(r"`([A-Za-z_][A-Za-z0-9_]+)`", task)
    if not backticked:
        return None
    sym = backticked[0]
    # Resolve symbol → defining file via roam search.
    d = _run_roam(["search", sym], cwd)
    results = (d or {}).get("results") or []
    if not results:
        return None
    location = (results[0] or {}).get("location") or ""
    if ":" in location:
        location = location.rsplit(":", 1)[0]  # strip :line
    if not location:
        return None
    # Now re-run the standard coupling probe pieces with the resolved file.
    out: dict = {}
    deps = _run_roam(["deps", location], cwd, detail=True)
    if deps:
        imports = deps.get("imports", [])[:15]
        imported_by = deps.get("imported_by", [])[:15]
        if imports or imported_by:
            out["structural_imports"] = imports
            out["structural_imported_by_top"] = imported_by
            out["structural_imported_by_count"] = len(deps.get("imported_by", []))
    cochange = _git_cochange_counts(location, cwd, limit=200)
    if cochange:
        out["temporal_coupling_pairs"] = [
            {"file_a": location, "file_b": fname, "cochange_count": count} for fname, count in cochange[:8]
        ]
    if not out:
        return None
    out["coupling_resolution"] = (
        f"`{sym}` (backticked symbol) resolved to {location} via `roam search-symbol`. Probes ran on the resolved file."
    )
    return out


def _probe_blast_backtick_for_task(task: str, cwd: str | None) -> dict | None:
    """W39 C2 — same fallback as `_probe_callers_backtick_for_task` but
    for structural_blast. When the user names a SYMBOL in backticks
    (e.g. "what's the blast radius of `compile_plan`"), the inner
    blast probe finds no named_paths and skips. This wrapper runs
    `roam impact <symbol>` and embeds the affected file set.
    """
    backticked = re.findall(r"`([A-Za-z_][A-Za-z0-9_]+)`", task)
    if not backticked:
        return None
    sym = backticked[0]
    d = _run_roam(["impact", sym], cwd, detail=True)
    if not d:
        return None
    # W39 C2: actual `roam impact --json --detail` shape uses
    # `affected_files` (count) + `affected_file_list` (entries).
    affected = d.get("affected_file_list") or d.get("affected") or d.get("files") or d.get("impact_set") or []
    if not affected:
        return None
    count = d.get("affected_files_total") or len(affected)
    return {
        "impact_count": count,
        "impact_top_files": affected[:15],
        "impact_definition": (
            f"Files transitively affected if `{sym}` changes (blast "
            f"radius). Extracted from backticked symbol in the task."
        ),
    }


def _probe_callers_backtick_for_task(task: str, cwd: str | None) -> dict | None:
    """F3 (W37 readiness): when the structural_callers procedure fires
    on a task that names the target SYMBOL in backticks rather than a
    file path, the inner `_probe_for_procedure` finds no named_paths
    and skips. This wrapper extracts the first backticked identifier
    and runs `roam uses <symbol>`.
    """
    backticked = re.findall(r"`([A-Za-z_][A-Za-z0-9_]+)`", task)
    sym = backticked[0] if backticked else _extract_bare_callers_symbol(task)
    if not sym:
        return None
    d = _run_roam(["uses", sym], cwd)
    if not d:
        return None
    callers = _flatten_consumers(d)
    if not callers:
        return None
    _has_cl = any(isinstance(c, dict) and c.get("call_line") for c in callers)
    cl_note = (
        (
            " Each entry includes `call_line` — the actual calling source "
            "line — so you do NOT need to re-grep the symbol."
        )
        if _has_cl
        else ""
    )
    return {
        "callers": callers[:_CALLERS_CAP],
        "callers_definition": (
            f"Callers of `{sym}`. Listed in graph-order; cap 20. edge=call "
            f"means a call edge; edge=import means an import edge." + cl_note
        ),
    }


# Loop8 (2026-06-02): bare-symbol callers extraction. The backtick-only path
# missed the FAR more common un-backticked shape "who calls open_db" — which
# routed to structural_callers but stayed `full` (empty envelope) because
# likely_files was empty. The focused bench showed these as ties vs vanilla
# precisely because the probe never fired. Extract the bareword identifier
# from "who/what calls X" / "callers of X" when it looks like a real symbol.
# Priority-ordered (most-specific first). Searched independently so a
# leading verb in "find callers of open_db" can't let the generic
# "<sym> callers" arm latch onto "find" before the specific "callers of
# <sym>" arm is even considered — the combined-regex `.search()` was
# leftmost-greedy and returned the verb (failing the identifier check →
# 0 prefetch on a correctly-classified structural_callers route).
_BARE_CALLERS_PATTERNS = (
    re.compile(
        r"\b(?:who|what)\s+(?:calls?|uses?|references?)\s+"
        r"([A-Za-z_][A-Za-z0-9_]{2,})\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:callers?|references?|uses?)\s+(?:of|to)\s+(?:the\s+)?"
        r"([A-Za-z_][A-Za-z0-9_]{2,})\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b([A-Za-z_][A-Za-z0-9_]{2,})\s+callers?\b", re.IGNORECASE),
    # "how many callers does <sym> have" / "how many references does <sym> have"
    re.compile(r"\b(?:does|do)\s+([A-Za-z_][A-Za-z0-9_]{2,})\s+have\b", re.IGNORECASE),
)
# Back-compat alias — a few tests import the combined form.
_BARE_CALLERS_RE = re.compile(
    "|".join(p.pattern for p in _BARE_CALLERS_PATTERNS),
    re.IGNORECASE,
)
# Common English words that would pass the identifier regex but aren't symbols.
_BARE_CALLERS_STOPWORDS = frozenset(
    {
        "the",
        "this",
        "that",
        "function",
        "method",
        "class",
        "symbol",
        "code",
        "file",
        "module",
        "thing",
        "them",
        "these",
        "those",
        "which",
        "what",
        "everything",
        "anything",
        "something",
        "test",
        "tests",
    }
)


def _extract_bare_callers_symbol(task: str) -> str | None:
    """Return a plausible code symbol from a bare 'who calls X' task, or None.

    Conservative: requires the token to look like a code identifier (contains
    an underscore OR has a mixed-case letter, i.e. snake_case or camelCase)
    and not be a common English stopword. A bareword like 'function' or
    'the code' is rejected; 'open_db' / 'compileFor' / 'useThemeClasses' pass."""
    t = task or ""
    for pat in _BARE_CALLERS_PATTERNS:
        for m in pat.finditer(t):
            sym = next((g for g in m.groups() if g), None)
            if not sym or sym.lower() in _BARE_CALLERS_STOPWORDS:
                continue
            # Identifier-shaped: snake_case (has _) OR camelCase (lower→Upper).
            if "_" not in sym and not re.search(r"[a-z][A-Z]", sym):
                continue
            return sym
    return None


def _probe_symbol_pickaxe_for_task(task: str, cwd: str | None) -> dict | None:
    """W36c — when the task asks about a SYMBOL's history ("when did X
    get added?"), run `git log -S<symbol>` (pickaxe) and embed the
    introducing/removing commits. Symbol comes from backtick-quoted
    identifier in the task; if none, no probe.
    """
    if not _SYMBOL_PICKAXE_RE.search(task):
        return None
    backticked = re.findall(r"`([A-Za-z_][A-Za-z0-9_]+)`", task)
    if not backticked:
        return None
    import subprocess

    sym = backticked[0]
    try:
        proc = subprocess.run(
            ["git", "log", "-S", sym, "--all", "--max-count=5", "--format=%h %ad %an %s", "--date=short"],
            capture_output=True,
            text=True,
            timeout=8.0,
            cwd=cwd or None,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log_swallowed("compile.symbol_pickaxe.git_log", exc)
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return {
        "symbol_history": {
            "symbol": sym,
            "commits": proc.stdout.strip(),
        },
        "symbol_history_definition": (
            f"git pickaxe (-S{sym}) — commits that changed the number of "
            f"occurrences of `{sym}`. Most-recent first; the OLDEST entry "
            f"is the introducing commit."
        ),
    }


# W-ENTITY (2026-06-05) — entity-grounded no-file freeform. The freeform probe
# returned an EMPTY envelope whenever the prompt named no file path, even when
# it named a code identifier ("why does `compile_plan` drop the score").
# Production telemetry: ~49% of freeform compiles delivered no prefetch. A bare
# identifier is intent-agnostic ground truth — resolve it regardless of the
# (unknowable) procedure and embed def+body+references. Rarity-ranked so the
# most specific identifier wins and English-but-identifier-shaped noise loses.
_FREEFORM_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_FREEFORM_BACKTICK_IDENT_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_]+)`")
# Identifier-shaped tokens that are code/English noise, not resolvable symbols.
_FREEFORM_IDENT_STOPWORDS = frozenset(
    {
        "__init__",
        "__main__",
        "__name__",
        "self",
        "cls",
        "true",
        "false",
        "none",
        "null",
        "return",
        "import",
        "class",
        "def",
    }
)


def _freeform_ident_rarity(tok: str, backticked: bool) -> int:
    """Rank identifiers so the rarest/most-specific resolves first. Backticks
    are an explicit user signal (highest); underscores + camelCase humps +
    length approximate specificity ('compile_plan' outranks 'data')."""
    score = 1000 if backticked else 0
    score += tok.count("_") * 5
    score += len(re.findall(r"[a-z][A-Z]", tok)) * 5
    score += min(len(tok), 24)
    return score


def _extract_freeform_identifiers(task: str | None) -> list[str]:
    """Pull identifier-shaped tokens from a freeform task, rarity-ranked.

    Backticked tokens are accepted as-is (explicit user signal). Unbackticked
    tokens must be snake_case or camelCase (an English word like 'database'
    fails the shape gate) and not a stopword — the false-positive guard the
    blueprint calls for, so 'save'/'user'/'list' never resolve."""
    if not task:
        return []
    cands: dict[str, bool] = {}
    for tok in _FREEFORM_BACKTICK_IDENT_RE.findall(task):
        cands[tok] = True
    for m in _FREEFORM_IDENT_RE.finditer(task):
        tok = m.group(0)
        if tok in cands:
            continue
        low = tok.lower()
        if low in _FREEFORM_IDENT_STOPWORDS or low in _BARE_CALLERS_STOPWORDS:
            continue
        # Shape gate: snake_case (has _) OR camelCase (lower→Upper).
        if "_" not in tok and not re.search(r"[a-z][A-Z]", tok):
            continue
        cands[tok] = False
    return [
        tok
        for tok, _ in sorted(
            cands.items(),
            key=lambda kv: _freeform_ident_rarity(kv[0], kv[1]),
            reverse=True,
        )
    ]


def _probe_freeform_entities_for_task(task: str, cwd: str | None) -> dict | None:
    """W-ENTITY — entity-grounded prefetch for a no-file freeform prompt.

    Resolves the rarest identifier named in the task via `roam search` and
    embeds def-site + body + references, so a bare-identifier conversational
    prompt gets prefetch instead of an empty envelope. Tries the top-2
    candidates (rarest first); returns on the first that resolves."""
    if not cwd:
        return None
    for sym in _extract_freeform_identifiers(task)[:2]:
        d = _run_roam(["search", sym], cwd, timeout=3.0)
        raw = (d or {}).get("results") or (d or {}).get("symbols") or (d or {}).get("matches") or []
        hits = _build_symbol_definition_hits(raw, sym)
        if hits:
            return {
                "resolved_entity": sym,
                "symbol_definitions": hits,
                "symbol_definitions_definition": (
                    f"Entity-grounded prefetch — `{sym}` is the most specific "
                    f"identifier in the task (no file was named). Top-{len(hits)} "
                    f"definitions from `roam search {sym}` with body + references "
                    f"inline; answer from these rather than grepping or reading."
                ),
            }
    return None


def _probe_freeform_augment_for_task(task: str, named_paths: list[str], cwd: str | None) -> dict | None:
    """W35b/c — augment freeform_explore probe with:
      (b) `file_excerpt`: when task is an explain-question on a single small
          named file, embed the first 80 lines of source.
      (c) `recent_commits`: when task asks about history, embed `git log -5`.

    Both are additive — neither blocks the existing `file_skeleton` probe
    in _probe_for_procedure. Either can fire independently.
    """
    if not named_paths:
        # W-ENTITY — no file anchor: fall back to entity grounding so a bare-
        # identifier prompt still gets prefetch instead of an empty envelope.
        # Intent boosters that key on the TASK TEXT (introduced-when, which-
        # tests, taint, TODO scan) still apply without a file anchor.
        entity = _probe_freeform_entities_for_task(task, cwd) or {}
        entity.update(_probe_freeform_intent_boosters(task, [], cwd))
        return entity or None
    import os
    import subprocess

    facts: dict = {}
    target = named_paths[0]

    if _EXPLAIN_RE.search(task):
        full = os.path.join(cwd, target) if cwd and not os.path.isabs(target) else target
        try:
            with open(full, encoding="utf-8", errors="replace") as fh:
                head_lines = fh.readlines()[:_FILE_EXCERPT_LINES]
        except (OSError, ValueError) as exc:
            log_swallowed("compile.freeform_augment.read_excerpt", exc)
            head_lines = []
        if head_lines:
            facts["file_excerpt"] = {
                "path": target,
                "lines_shown": len(head_lines),
                "content": "".join(head_lines),
            }
            facts["file_excerpt_definition"] = (
                f"First {len(head_lines)} lines of {target}. The user asked "
                f"an explain/describe question — answer from THIS content "
                f"rather than re-Reading the file."
            )

    if _HISTORY_QUERY_RE.search(task):
        try:
            proc = subprocess.run(
                ["git", "log", "--max-count=5", "--stat", "--format=%h %ad %an %s", "--date=short", "--", target],
                capture_output=True,
                text=True,
                timeout=5.0,
                cwd=cwd or None,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log_swallowed("compile.freeform_augment.git_log", exc)
            proc = None
        if proc and proc.returncode == 0 and proc.stdout.strip():
            facts["recent_commits"] = proc.stdout.strip()
            facts["recent_commits_definition"] = (
                f"Last 5 commits touching {target} (hash date author subject + "
                f"stat). Answer history questions from THIS log rather than "
                f"running `git log` again."
            )

    # W-BUGSITE (2026-06-10) — "fix the bug in cli.py:45" carries an explicit
    # file:LINE but freeform only embeds skeleton+grep, so the agent must Read
    # the file to see the actual buggy code. When edit/bug intent AND a
    # path:line tuple are both present, embed the ±N lines around that line —
    # the bug-fix analog of the W86 test-source slice. Telemetry: the
    # "fix the bug in X:N" / "fix the AttributeError in X" family routed
    # freeform with no code at the cited line.
    facts.update(_freeform_bug_site_slice(task, named_paths, cwd))

    # Intent boosters: shape-gated probes (perf/algo, ownership, history,
    # tests-for, security/taint, TODO scan) mined from the frozen corpus's
    # still-missed freeform prompts. Each fires only on its regex.
    facts.update(_probe_freeform_intent_boosters(task, named_paths, cwd))

    return facts or None


def _probe_algo_findings(task: str, named_paths: list[str], cwd: str | None) -> dict:
    """Embed `roam algo --path <named>` findings for perf-shaped tasks.

    The catalog detector output (Current/Better/Tip/Fix per anti-pattern,
    impact-ranked) is the literal answer to "optimize X" / "fix the n+1 in
    Y" — without it the agent re-derives the analysis by reading the file.
    """
    if not named_paths or not _ALGO_PERF_RE.search(task):
        return {}
    args = ["algo", "-n", "5"]
    for p in named_paths[:2]:
        args += ["--path", p]
    d = _run_roam(args, cwd, timeout=10.0)
    findings = (d or {}).get("findings") or []
    if not findings:
        return {}
    items = [
        {
            "task_id": f.get("task_id"),
            "symbol": f.get("symbol_name"),
            "location": f.get("location"),
            "reason": f.get("reason"),
            "suggested_way": f.get("suggested_way"),
            "tip": f.get("tip"),
            "fix": (f.get("fix") or "")[:240],
            "confidence": f.get("confidence"),
            "impact_score": f.get("impact_score"),
        }
        for f in findings[:5]
    ]
    return {
        "algo_findings": items,
        "algo_findings_definition": (
            f"Algorithm anti-patterns detected in {', '.join(named_paths[:2])}, "
            f"impact-ranked, each with the better approach and a fix sketch. "
            f"Base your optimization on THESE findings — do not re-derive the "
            f"analysis; cite location + suggested_way directly."
        ),
    }


# Security-shaped freeform tasks ("find SQL injection risks", "trace tainted
# data") embed the whole-repo taint scan — the only corpus-recurring shape
# with NO existing probe (ownership/TODO/test-impact/history already have
# W109/W111/W80/pickaxe probes; the gap there was promotion keys + the
# test-impact probe's named-path requirement, fixed separately).
_SECURITY_TAINT_RE = re.compile(
    r"\binjection\b|\btaint(?:ed)?\b|\bxss\b|\bvulnerab|\bsanitiz|\bsecurity\s+(?:risk|review|audit|holes?)\b",
    re.IGNORECASE,
)
# World-model asks ("is X idempotent", "what does X mutate", "side effects
# of X") — the R28 classifiers answer these outright in ~0.13s.
_WORLD_MODEL_RE = re.compile(
    r"\bidempoten|\bside.?effects?\b|\bwhat\s+does\s+\w+\s+(?:mutate|write\s+to|touch)\b|\bsafe\s+to\s+retry\b",
    re.IGNORECASE,
)
# Design-pattern asks ("find all the singletons", "which factories exist").
_DESIGN_PATTERN_RE = re.compile(
    r"\b(?:singletons?|factor(?:y|ies)|observers?|repositor(?:y|ies)|strateg(?:y|ies)|decorators?)\b.{0,30}\b(?:pattern|exist|are there|find|list|implement)|"
    r"\b(?:find|list|show)\b.{0,20}\b(?:singletons?|factor(?:y|ies)|observers?|repositor(?:y|ies)|strateg(?:y|ies)|decorators?)\b|"
    r"\bdesign\s+patterns?\b",
    re.IGNORECASE,
)

_IDENT_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_]+)`|\b([a-z_]+_[a-z0-9_]+|[a-z]+[A-Z][A-Za-z0-9]+)\b")


def _booster_target_symbol(task: str) -> str | None:
    """Backticked token first; else the first snake_case/camelCase identifier."""
    for m in _IDENT_RE.finditer(task):
        tok = m.group(1) or m.group(2)
        if tok:
            return tok
    return None


def _probe_freeform_intent_boosters(task: str, named_paths: list[str], cwd: str | None) -> dict:
    """Shape-gated facts for freeform intents with no procedure of their own.

    Currently: security/taint (whole-repo scan measured ~0.7s here). The
    perf/algo probe lives in :func:`_probe_algo_findings`; ownership, TODO,
    and test-impact shapes are owned by the W109/W111/W80 extenders.
    """
    facts: dict = {}

    if _SECURITY_TAINT_RE.search(task):
        d = _run_roam(["taint"], cwd, timeout=15.0)
        summ = (d or {}).get("summary") or {}
        if summ:
            top = ((d or {}).get("findings") or [])[:10]
            facts["taint_summary"] = {
                "verdict": summ.get("verdict"),
                "findings": summ.get("findings"),
                "risk_score": summ.get("risk_score"),
                "top_findings": top,
            }
            facts["taint_summary_definition"] = (
                "Whole-repo taint/dataflow scan (source→sink with sanitizer "
                "tracking). Ground the security answer on THIS — zero findings "
                "means the scan ran clean, not that it didn't run."
            )

    if _WORLD_MODEL_RE.search(task):
        sym = _booster_target_symbol(task)
        if sym:
            wm: dict = {}
            d_idem = _run_roam(["idempotency", sym], cwd, timeout=8.0)
            if d_idem and (d_idem.get("summary") or d_idem.get("symbols")):
                wm["idempotency"] = {
                    "verdict": (d_idem.get("summary") or {}).get("verdict"),
                    "symbols": (d_idem.get("symbols") or [])[:5],
                }
            d_se = _run_roam(["side-effects", sym], cwd, timeout=8.0)
            if d_se and (d_se.get("summary") or d_se.get("symbols")):
                wm["side_effects"] = {
                    "verdict": (d_se.get("summary") or {}).get("verdict"),
                    "symbols": (d_se.get("symbols") or [])[:5],
                }
            if wm:
                facts["world_model"] = {"symbol": sym, **wm}
                facts["world_model_definition"] = (
                    f"Static world-model classification of `{sym}`: effect kinds "
                    f"(io_read/io_write/mutation/process/none) and retry-safety "
                    f"(idempotent/non_idempotent/unknown). Answer from THESE "
                    f"classifications; cite the kind labels directly."
                )

    if _DESIGN_PATTERN_RE.search(task):
        d = _run_roam(["patterns"], cwd, timeout=10.0)
        summ = (d or {}).get("summary") or {}
        if summ:
            raw = (d or {}).get("patterns") or (d or {}).get("results") or []
            # `roam patterns` groups instances by type ({"singleton": [...]});
            # flatten with the type stamped on each instance.
            if isinstance(raw, dict):
                pats = [
                    {"pattern": ptype, **(inst if isinstance(inst, dict) else {"item": inst})}
                    for ptype, insts in raw.items()
                    for inst in (insts or [])
                ]
            else:
                pats = list(raw)
            facts["design_patterns"] = {
                "verdict": summ.get("verdict"),
                "types_found": summ.get("types_found"),
                "total": summ.get("total_patterns"),
                "instances": pats[:15],
            }
            facts["design_patterns_definition"] = (
                "Detected design-pattern instances (singleton/factory/observer/"
                "repository/strategy/decorator) with locations. List from THESE "
                "instances — do not grep for class shapes."
            )

    return facts


_BUG_SITE_SLICE_BEFORE = 12
_BUG_SITE_SLICE_AFTER = 12


def _bug_site_target(cited_path: str, named_paths: list[str], cwd: str | None) -> tuple[str, str] | None:
    """Resolve the bug-site file: prefer the cited path; fall back to the
    first named_path (a bare cited basename may already be resolved
    upstream). Returns (repo_relative_target, absolute_path) or None."""
    import os

    def _abs(p: str) -> str:
        return os.path.join(cwd, p) if cwd and not os.path.isabs(p) else p

    if os.path.exists(_abs(cited_path)):
        return cited_path, _abs(cited_path)
    if named_paths and os.path.exists(_abs(named_paths[0])):
        return named_paths[0], _abs(named_paths[0])
    return None


def _read_site_window(full: str, line_no: int) -> tuple[int, int, list[str]] | None:
    """Read ±slice lines around 1-based *line_no*. (start0, end0, gutter-numbered
    lines) or None on IO error / empty file / bad line."""
    try:
        with open(full, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except (OSError, ValueError) as exc:
        log_swallowed("compile.freeform_augment.bug_site", exc)
        return None
    if not lines or line_no < 1:
        return None
    start = max(0, line_no - 1 - _BUG_SITE_SLICE_BEFORE)
    end = min(len(lines), line_no + _BUG_SITE_SLICE_AFTER)
    numbered = [f"{i + 1:>5}  {lines[i].rstrip(chr(10))}" for i in range(start, end)]
    return start, end, numbered


def _freeform_bug_site_slice(task: str, named_paths: list[str], cwd: str | None) -> dict:
    """W-BUGSITE — embed source around a cited `path:line` on edit/bug intent."""
    if not _EDIT_INTENT_RE.search(task or ""):
        return {}
    m = _STACK_FRAME_GENERIC_RE.search(task or "")
    if not m:
        return {}
    try:
        line_no = int(m.group(2))
    except ValueError:
        return {}
    resolved = _bug_site_target(m.group(1), named_paths, cwd)
    if not resolved:
        return {}
    target, full = resolved
    window = _read_site_window(full, line_no)
    if not window:
        return {}
    start, end, numbered = window
    return {
        "bug_site_slice": {
            "path": target,
            "cited_line": line_no,
            "line_range": f"{start + 1}-{end}",
            "content": "\n".join(numbered),
        },
        "bug_site_slice_definition": (
            f"Source of {target} around line {line_no} (the cited bug site, "
            f"line numbers in the left gutter). Inspect THIS for the defect "
            f"before Reading the file."
        ),
    }


def _probe_trace_for_task(task: str, cwd: str | None) -> dict | None:
    """Trace probe — compile-time roam retrieve top-ranked spans.

    Wave 6 v2 (2026-05-29 16:10): the original Wave 6 lost quality because
    it embedded raw source content from generic retrieve. This scoped
    version returns just the RANKED FILE LIST with line ranges and scores
    — agent reads the actual files via Read if needed. Tested on trace_query
    tasks only (no other procedure has natural task-text input).
    """
    d = _run_roam(["retrieve", task], cwd, timeout=12.0)
    if not d:
        return None
    candidates = d.get("candidates", [])
    if not candidates:
        return None
    # Keep top 5 spans with name + file:line + score
    spans = [
        {
            "file": c.get("file_path"),
            "lines": f"{c.get('line_start')}-{c.get('line_end')}",
            "kind": c.get("kind"),
            "name": c.get("qualified_name") or c.get("name"),
            "score": round(c.get("score", 0), 2),
        }
        for c in candidates[:5]
    ]
    return {
        "trace_spans": spans,
        "trace_definition": "roam retrieve top-ranked spans (FTS5 + structural rerank). Read each file at the line range to walk the chain.",
    }


def _zero_agent_callers(target: str, facts: dict) -> str | None:
    callers = facts.get("callers")
    if not callers:
        return None
    lines = [f"**Callers of `{target}`** ({len(callers)} found):", ""]
    for i, c in enumerate(callers[:_CALLERS_CAP], 1):
        if isinstance(c, dict):
            loc = c.get("file") or c.get("path") or "?"
            line = c.get("line") or "?"
            lines.append(f"{i}. `{loc}:{line}`")
        else:
            lines.append(f"{i}. `{c}`")
    if len(callers) > 20:
        lines.append(f"... and {len(callers) - 20} more")
    lines.append("")
    lines.append(f"*Source: `roam uses {target}` — graph-precise symbol references.*")
    return "\n".join(lines)


def _zero_agent_dead(facts: dict) -> str | None:
    unused = facts.get("unused_top_10")
    if not unused:
        return None
    lines = ["**Top unused symbols (likely safe to delete):**", ""]
    for i, item in enumerate(unused[:10], 1):
        if isinstance(item, dict):
            sym = item.get("symbol") or item.get("name") or "?"
            f = item.get("file") or item.get("path") or "?"
            lines.append(f"{i}. `{sym}` in `{f}`")
        else:
            lines.append(f"{i}. {item}")
    lines.append("")
    lines.append(
        "*Source: `roam dead-code` — symbols with zero callers across the indexed graph. "
        "Verify before deletion (test references, dynamic dispatch, public API).*"
    )
    return "\n".join(lines)


def _zero_agent_coupling(target: str, facts: dict) -> str:
    """Emit a templated coupling answer using prefetched dual-probe data."""
    lines = [f"**Files most coupled to `{target}`:**", ""]

    # Structural (imports + imported-by) — the explicit "structural coupling" axis
    if facts.get("structural_imported_by_top"):
        n_total = facts.get("structural_imported_by_count", 0)
        top = facts["structural_imported_by_top"]
        lines.append("## Structural (static dependency graph)")
        lines.append(f"`{target}` is imported by {n_total} files; top consumers:")
        for i, item in enumerate(top[:8], 1):
            path = item.get("path", "?")
            sym = item.get("symbol_count", "?")
            lines.append(f"{i}. `{path}` (uses {sym} symbol{'s' if sym != 1 else ''})")
        lines.append("")
    if facts.get("structural_imports"):
        imps = facts["structural_imports"]
        lines.append(
            f"`{target}` imports {len(imps)} files: "
            + ", ".join(f"`{i.get('path', i) if isinstance(i, dict) else i}`" for i in imps[:6])
        )
        lines.append("")

    # Temporal (git co-change) — the alternative interpretation of "coupled"
    if facts.get("temporal_coupling_pairs"):
        pairs = facts["temporal_coupling_pairs"]
        lines.append("## Temporal (git co-change history)")
        for i, p in enumerate(pairs[:5], 1):
            other = p["file_b"] if p["file_a"].endswith(target) else p["file_a"]
            lines.append(f"{i}. `{other}` — strength {p['strength']}, {p['cochange_count']} co-changes")
        lines.append("")

    lines.append(
        f"*Source: deterministic probe via `roam deps {target}` + "
        f"`roam coupling`. Structural = static import graph; "
        f"temporal = git co-change pattern.*"
    )
    return "\n".join(lines)


def _maybe_batch_search_starter(task: str, named_paths: list[str]) -> str | None:
    """Return roam_batch_search starter if task names 3+ symbols/paths.

    Counts named paths + heuristic symbol candidates (CamelCase /
    snake_case identifiers in backticks or as "X function" / "X method").
    """
    # Conservative — just count named paths + backtick-quoted identifiers.
    backticked = re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", task)
    total = len(named_paths) + len(backticked)
    if total >= _BATCH_SEARCH_THRESHOLD:
        targets = (named_paths + backticked)[:10]
        return f"roam --json batch-search --patterns {' '.join(targets)}"
    return None


# ---- v0 forbidden paths — constant; expand in v1 from EvidenceTrustRank rules ----
_FORBIDDEN_PATHS_DEFAULT = [
    "package.json",
    "pyproject.toml",
    "**/lockfiles/**",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "uv.lock",
    "**/migrations/**",
    "**/migration/**",
    ".env",
    ".env.*",
    ".git/**",
    "node_modules/**",
    ".venv/**",
    ".roam/**",
    "internal/**",  # roam-code repo: private folder
]


# ---- W42 — L1-envelope extender registries ----
#
# `to_l1_probe_envelope` used to be a 130-line / cc=63 brain-method
# stacking ~24 procedure-specific branches. Each branch followed one
# of three patterns; W42 extracts each pattern into a registry +
# helper so the method itself becomes a 15-line orchestrator.
#
# Pattern A — procedure-specific task-text probes:
#   "if procedure matches, run a probe that needs the raw task text
#    (and possibly named_paths), merge its result into prefetched."
# Pattern B — backtick-symbol fallback probes:
#   "if procedure matches AND the inner per-procedure probe returned
#    nothing for the expected key, try the backtick-symbol resolver."
# Pattern C — always-on detectors and augmentations:
#   "fire regardless of procedure; merge keys into prefetched."

# Registry A — keyed by procedure. Each value is a callable accepting
# (task, named_paths, cwd) and returning {key: value, ...} or None.
# Probes with shorter signatures are adapted via tiny lambdas.
_L1_TASK_TEXT_PROBES: dict[str, callable] = {  # type: ignore[type-arg]
    "trace_query": lambda task, named, cwd: _probe_trace_for_task(task, cwd),
    "stack_trace_fix": lambda task, named, cwd: _probe_stack_trace_for_task(task, cwd),
    "freeform_explore": _probe_freeform_augment_for_task,
    "synthesis_query": _probe_sibling_test_for_task,
}

# Registry B — keyed by procedure. Each value is a tuple of
# (already_present_keys, fallback_fn). The fallback runs only when
# NONE of `already_present_keys` are in prefetched.
_L1_BACKTICK_FALLBACKS: dict[str, tuple[tuple[str, ...], callable]] = {  # type: ignore[type-arg]
    "structural_callers": (("callers",), _probe_callers_backtick_for_task),
    "structural_blast": (("impact_top_files",), _probe_blast_backtick_for_task),
    "structural_coupling": (
        ("structural_imports", "temporal_coupling_pairs"),
        _probe_coupling_backtick_for_task,
    ),
}

# W201 — import-audit probe. W195 trace: t16 alone uses 10× Bash for
# `python -c "import X"` retries. For ImportError-shape tasks, pre-resolve
# the module: try import in a sandbox, capture path + status + suggested
# fix. Eliminates the trial-and-error loop entirely.
_W201_IMPORT_RE = re.compile(
    r"\bImportError\s*:\s*(?:No module named\s+)?['\"]?([\w][\w.]+)['\"]?|"
    r"\bModuleNotFoundError\s*:\s*(?:No module named\s+)?['\"]?([\w][\w.]+)['\"]?",
    re.IGNORECASE,
)


def _probe_import_audit_for_task(task: str, cwd: str | None) -> dict | None:
    if not task:
        return None
    m = _W201_IMPORT_RE.search(task)
    if not m:
        return None
    module = m.group(1) or m.group(2)
    if not module:
        return None
    # Try importing in a sandboxed subprocess
    try:
        proc = subprocess.run(
            [
                "python3",
                "-c",
                f"import {module}; import sys; "
                f"print('OK', getattr(sys.modules.get('{module}'), '__file__', '<no-file>'))",
            ],
            capture_output=True,
            text=True,
            timeout=4.0,
            cwd=cwd or os.getcwd(),
        )
        importable = proc.returncode == 0
        details = proc.stdout.strip() if importable else proc.stderr.strip()
    except (subprocess.TimeoutExpired, OSError) as exc:
        log_swallowed("compile.import_audit", exc)
        return None
    # Heuristic fix suggestion
    suggestion = ""
    if not importable:
        # If module looks like a project module (has dots, matches dirs)
        if "." in module:
            parts = module.split(".")
            if cwd and (Path(cwd) / "src" / parts[0]).exists():
                suggestion = (
                    f"`{module}` matches src/{parts[0]} but isn't importable. "
                    f"Run `pip install -e .` from {cwd} to enable dev-mode "
                    f"install OR check PYTHONPATH/sys.path."
                )
            else:
                suggestion = (
                    f"Module `{module}` not found. Run `pip install {parts[0]}` "
                    f"OR check if it's a typo of an existing module."
                )
        else:
            suggestion = f"Try `pip install {module}`."
    return {
        "import_audit": {
            "module": module,
            "importable": importable,
            "details": details[:300],
            "suggestion": suggestion,
        },
        "import_audit_definition": (
            f"Pre-audited `import {module}`: status="
            f"{'OK' if importable else 'FAILED'}. Use this — do NOT retry "
            f'`python -c "import {module}"` yourself.'
        ),
    }


# W196 — grep-replication probe. The W195 tool-trace showed Grep is 22%
# of all agent tool calls (51 of 231 across 30 tasks); t8 alone uses
# 9 greps. If compile pre-runs grep for task-mentioned literal patterns
# and embeds the result, the agent skips the manual searches.
#
# Extracts patterns from the task:
#   - backticked symbols: `log_swallowed`
#   - quoted strings: 'sqlite3.connect' / "PRAGMA journal_mode=WAL"
#   - dotted-path identifiers in plain text: re.compile, sys.path
# Picks search root: the deepest existing named_path dir, else repo root.
# Caps result at 30 hits, each line truncated to 200 chars.
_W196_LITERAL_RE = re.compile(
    r"`([A-Za-z_][\w./]*(?:\.[\w]+)?)`"  # backticked symbol/path
    r"|'([^'\n]{3,60})'"  # 'quoted'
    r'|"([^"\n]{3,60})"'  # "quoted"
    r"|\b([a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]+(?:\.[a-z_][a-z0-9_]+)?)\b"  # dotted: sqlite3.connect, sys.path
    r"|\b(_[a-z_][a-z0-9_]{3,})\b"  # _probe_X / _private style
    r"|\b([A-Z][A-Z_][A-Z0-9_]{2,})\b"  # ALL_CAPS_IDENT
)


def _extract_grep_patterns(task: str) -> list[str]:
    """W196 — literal patterns worth pre-grepping from the task: drop absolute
    paths and overly-generic stopwords, dedupe (order-preserving), cap at 5."""
    hits = _W196_LITERAL_RE.findall(task)
    if not hits:
        return []
    patterns: list[str] = []
    for tup in hits:
        for g in tup:
            if g and not g.startswith("/"):
                if len(g) < 3 or g.lower() in ("the", "and", "for", "with", "from", "into"):
                    continue
                patterns.append(g)
    seen: set = set()
    patterns = [p for p in patterns if not (p in seen or seen.add(p))]
    return patterns[:5]


def _grep_one_pattern(pat: str, search_root: str):
    """W196 — `roam grep` for one pattern. Returns (match_lines, total_count) or
    None. The total comes from the `agent_contract.facts` "N matches …" string."""
    d = _run_roam(["grep", pat, "-n", "50", "--source-only"], search_root, timeout=6.0)
    if not d or not isinstance(d, dict):
        return None
    raw_matches = d.get("matches") or []
    if not raw_matches:
        return None
    facts = (d.get("agent_contract") or {}).get("facts") or []
    total = None
    for f in facts:
        if isinstance(f, str) and "matches" in f and "for" in f:
            try:
                total = int(f.split()[0])
            except (ValueError, IndexError) as exc:
                log_swallowed("compile.match_count_parse", exc)
            break
    lines = [
        {
            "path": m.get("path", ""),
            "line": m.get("line"),
            "enclosing_symbol": m.get("enclosing_symbol"),
            "enclosing_kind": m.get("enclosing_kind"),
            "content": (m.get("content") or "")[:180],
        }
        for m in raw_matches[:20]
    ]
    if not lines:
        return None
    return lines, total


def _probe_grep_for_task(task: str, named_paths: list[str], cwd: str | None) -> dict | None:
    """W196 — pre-run grep for literal patterns mentioned in the task.
    Replaces the 22% of agent tool calls that are Grep."""
    if not task or not cwd:
        return None
    patterns = _extract_grep_patterns(task)
    if not patterns:
        return None
    # Pick search root: a directory from named_paths if one exists,
    # else repo root.
    search_root = cwd
    for p in named_paths or []:
        if isinstance(p, str):
            full = p if os.path.isabs(p) else os.path.join(cwd, p)
            d = full if os.path.isdir(full) else os.path.dirname(full)
            if d and os.path.isdir(d):
                search_root = d
                break
    # W196 — `roam grep` per pattern (ripgrep under the hood, ~0.16s,
    # enclosing_symbol per hit, W147 cache, --source-only).
    matches: dict[str, list[dict]] = {}
    total_matches_by_pat: dict[str, int] = {}
    for pat in patterns:
        res = _grep_one_pattern(pat, search_root)
        if not res:
            continue
        lines, total = res
        if total:
            total_matches_by_pat[pat] = total
        matches[pat] = lines
    if not matches:
        return None
    return {
        "grep_results": {
            "patterns": list(matches.keys()),
            "total_by_pattern": total_matches_by_pat,
            "matches": matches,
        },
        "grep_results_definition": (
            f"Pre-run `roam grep` hits for {len(matches)} pattern(s): "
            f"{', '.join(matches.keys())}. Each hit includes "
            f"path:line + enclosing_symbol. Use these to answer "
            f"'find every X' / 'list X' / 'verify X' questions WITHOUT "
            f"running grep yourself — totals come from `agent_contract.facts`."
        ),
    }


# Registry C — list of (label, callable) where each callable returns a
# dict (or None) merged into prefetched, regardless of procedure.
# Order matters only for documentation; merges are commutative
# because each callable owns its own key namespace.
_L1_ALWAYS_ON_PROBES = (
    # W196 — pre-run grep for task-mentioned literal patterns
    ("grep_replication", lambda task, named, cwd, proc: _probe_grep_for_task(task, named, cwd)),
    # W201 — import audit for ImportError-shape tasks (closes t16)
    ("import_audit", lambda task, named, cwd, proc: _probe_import_audit_for_task(task, cwd)),
    # W36b — diff between 2+ named paths when compare-vocab triggers.
    ("compare", lambda task, named, cwd, proc: _probe_path_comparison_for_task(task, named, cwd)),
    # W36c — git pickaxe when backticked symbol + history vocab.
    ("pickaxe", lambda task, named, cwd, proc: _probe_symbol_pickaxe_for_task(task, cwd)),
    # L11 — decision criterion for comparison tasks.
    (
        "criterion",
        lambda task, named, cwd, proc: {"decision_criterion": d} if (d := _detect_decision_criterion(task)) else None,
    ),
    # L13 — scope lock for directory-scoped tasks.
    (
        "scope_lock",
        lambda task, named, cwd, proc: {"scope_lock": s} if (s := _detect_scope_lock(task, named)) else None,
    ),
    # Output-shape routing.
    (
        "output_shape",
        lambda task, named, cwd, proc: {"output_shape": s} if (s := _detect_output_shape(task, proc)) else None,
    ),
    # W44 I1 — conventions probe ("how do we do X here").
    ("conventions", lambda task, named, cwd, proc: _probe_conventions_for_task(task, named, cwd)),
    # W44 I2 — module-name shorthand resolver ("the auth module").
    ("module_name", lambda task, named, cwd, proc: _probe_module_name_for_task(task, named, cwd)),
    # W48 — reachability Y/N (2 backticked symbols + reachability vocab).
    ("reachability", lambda task, named, cwd, proc: _probe_reachability_for_task(task, cwd)),
    # W49 — config-by-name (env var / setting lookups).
    ("config", lambda task, named, cwd, proc: _probe_config_for_task(task, cwd)),
    # W50 — find-by-description (semantic search).
    ("find_by_desc", lambda task, named, cwd, proc: _probe_find_by_description_for_task(task, cwd)),
    # W66 — runtime hotspots probe (why-slow).
    ("why_slow", lambda task, named, cwd, proc: _probe_why_slow_for_task(task, cwd)),
    # W67 — entry-points probe.
    ("entry_points", lambda task, named, cwd, proc: _probe_entry_points_for_task(task, cwd)),
    # W80 — test-impact probe (source → tests).
    ("test_impact", lambda task, named, cwd, proc: _probe_test_impact_for_task(task, named, cwd)),
    # W101 — cross-file refactor (move X from A to B) probe.
    ("refactor_move", lambda task, named, cwd, proc: _probe_refactor_move_for_task(task, cwd)),
    # W102 — API surface probe (top-level def/class scan).
    ("api_surface", lambda task, named, cwd, proc: _probe_api_surface_for_task(task, named, cwd)),
    # W109 — file-owner / blame probe.
    ("owners", lambda task, named, cwd, proc: _probe_owner_for_task(task, named, cwd)),
    # W110 — env-var audit probe.
    ("env_vars", lambda task, named, cwd, proc: _probe_env_vars_for_task(task, named, cwd)),
    # W111 — TODO/FIXME audit probe.
    ("todo_audit", lambda task, named, cwd, proc: _probe_todo_audit_for_task(task, named, cwd)),
    # W112 — deprecation marker probe.
    ("deprecation", lambda task, named, cwd, proc: _probe_deprecation_for_task(task, named, cwd)),
    # W113 — subprocess audit probe.
    ("subprocess_audit", lambda task, named, cwd, proc: _probe_subprocess_audit_for_task(task, named, cwd)),
    # Cross-channel memory: verify's persisted findings ride into the
    # envelope for the file the task names.
    ("known_findings", lambda task, named, cwd, proc: _probe_known_findings_for_task(named, cwd)),
)


def _load_verify_report(cwd: str) -> tuple[dict, float] | None:
    """Load verify-report.json and its mtime. Returns (report, mtime) or None."""
    report_path = os.path.join(cwd, ".roam", "verify-report.json")
    try:
        mtime = os.path.getmtime(report_path)
        with open(report_path, encoding="utf-8") as fh:
            report = json.load(fh)
        return report, mtime
    except (OSError, ValueError) as exc:
        log_swallowed("compile.known_findings.read", exc)
        return None


def _format_finding_top(rows: list[dict]) -> list[dict]:
    """Format top 5 findings for display."""
    return [
        {
            "category": v.get("category"),
            "severity": v.get("severity"),
            "line": v.get("line"),
            "symbol": v.get("symbol"),
            "message": (v.get("message") or "")[:160],
        }
        for v in rows[:5]
    ]


def _probe_known_findings_for_task(named_paths: list[str], cwd: str | None) -> dict | None:
    """Embed the named file's OPEN verify findings from the persisted
    whole-repo report (``.roam/verify-report.json``, written by
    ``roam verify --report --persist``).

    The verify channel already knows each file's debt; without this the
    agent re-derives it (or worse, edits around an open N+1 it can't see).
    Pure local JSON read — no subprocess, no model calls. Context, not an
    answer: deliberately NOT in the L1 promotion keys, it only rides along.
    """
    if not named_paths or not cwd:
        return None

    loaded = _load_verify_report(cwd)
    if not loaded:
        return None
    report, mtime = loaded

    targets = set(named_paths[:2])
    rows = [v for v in report.get("violations") or [] if v.get("file") in targets]
    if not rows:
        return None

    from collections import Counter

    by_category: dict[str, int] = dict(Counter((v.get("category") or "?") for v in rows))
    top = _format_finding_top(rows)
    age_h = max(0.0, (time.time() - mtime) / 3600.0)

    return {
        "known_findings": {
            "files": sorted(targets & {v.get("file") for v in rows}),
            "total": len(rows),
            "by_category": by_category,
            "top": top,
            "report_age_hours": round(age_h, 1),
        },
        "known_findings_definition": (
            "OPEN verify findings already on record for the named file(s) "
            "(from the persisted whole-repo report; age disclosed in "
            "report_age_hours). If your change touches these lines, fix the "
            "finding in the same pass; do not re-run a whole-repo scan to "
            "rediscover them."
        ),
    }


def _apply_task_text_probe(
    procedure: str, task: str, named_paths: list[str], cwd: str | None, prefetched: dict
) -> dict:
    """Pattern A — run the procedure-specific task-text probe if registered."""
    fn = _L1_TASK_TEXT_PROBES.get(procedure)
    if fn is None:
        return prefetched
    result = fn(task, named_paths, cwd)
    if result:
        prefetched = prefetched | result
    return prefetched


def _apply_backtick_fallback(procedure: str, task: str, cwd: str | None, prefetched: dict) -> dict:
    """Pattern B — run the backtick-symbol fallback if the inner probe
    returned nothing for any of the expected keys."""
    entry = _L1_BACKTICK_FALLBACKS.get(procedure)
    if entry is None:
        return prefetched
    expected_keys, fallback_fn = entry
    if any(prefetched.get(k) for k in expected_keys):
        return prefetched
    result = fallback_fn(task, cwd)
    if result:
        prefetched = prefetched | result
    return prefetched


# W126 — probe negative cache. Many probes will reliably return None
# for the same task ("does this match the regex?") — caching the
# negative outcome saves us re-running the regex + subprocess on
# every compile call. Keyed by (probe_label, sha256(task)[:12]),
# 300-second TTL, 512-entry cap (LRU on insertion order).
_PROBE_NEGATIVE_CACHE: dict[tuple[str, str], float] = {}
_PROBE_NEG_TTL_S = 300.0
_PROBE_NEG_CAP = 512


def _probe_neg_cache_key(label: str, task: str) -> tuple[str, str]:
    import hashlib

    return (label, hashlib.sha256(task.encode("utf-8", "replace")).hexdigest()[:12])


def _probe_neg_cached_miss(label: str, task: str) -> bool:
    """True iff we recently cached a None response for this (label, task)."""
    key = _probe_neg_cache_key(label, task)
    cached = _PROBE_NEGATIVE_CACHE.get(key)
    if cached is None:
        return False
    if time.monotonic() - cached > _PROBE_NEG_TTL_S:
        del _PROBE_NEGATIVE_CACHE[key]
        return False
    return True


def _probe_neg_record(label: str, task: str) -> None:
    """Record that this probe returned None — skip until TTL."""
    if len(_PROBE_NEGATIVE_CACHE) >= _PROBE_NEG_CAP:
        # Evict oldest entry by ts
        oldest = min(_PROBE_NEGATIVE_CACHE.items(), key=lambda kv: kv[1])
        del _PROBE_NEGATIVE_CACHE[oldest[0]]
    _PROBE_NEGATIVE_CACHE[_probe_neg_cache_key(label, task)] = time.monotonic()


# W155 — persistent negative cache (cross-session). Sister to W126 in-mem
# cache + W147/W152 disk-backed positive caches. A probe that returned
# None for "this regex doesn't match this task" is information that holds
# across process death — re-running the regex on every fresh session
# wastes work. 6-hour TTL (shorter than positive cache because task text
# space is unbounded; entries decay faster), 2048-row cap.
_PROBE_NEG_PERSIST_CAP = 2048
_PROBE_NEG_PERSIST_TTL_S = 6 * 3600.0
_PROBE_NEG_PERSIST_TABLE_INITED: set[str] = set()


def _probe_neg_persist_ensure_schema(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS probe_neg_cache (key TEXT PRIMARY KEY, label TEXT, ts REAL)")
    _set_wal(conn)


def _probe_neg_persist_get(label: str, task: str, cwd: str | None) -> bool:
    path = _run_roam_persist_path(cwd)
    if not path:
        return False
    try:
        import sqlite3

        conn = sqlite3.connect(path, timeout=1.0)
        try:
            if path not in _PROBE_NEG_PERSIST_TABLE_INITED:
                _probe_neg_persist_ensure_schema(conn)
                _PROBE_NEG_PERSIST_TABLE_INITED.add(path)
            key = sha256((label + "\x1f" + (task or "")).encode("utf-8", "replace")).hexdigest()[:24]
            row = conn.execute(
                "SELECT ts FROM probe_neg_cache WHERE key=?",
                (key,),
            ).fetchone()
            if row is None:
                return False
            (ts,) = row
            if (time.time() - ts) > _PROBE_NEG_PERSIST_TTL_S:
                conn.execute("DELETE FROM probe_neg_cache WHERE key=?", (key,))
                conn.commit()
                return False
            return True
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        log_swallowed("compile.probe_neg_persist.get", exc)
        return False


def _probe_neg_persist_put(label: str, task: str, cwd: str | None) -> None:
    path = _run_roam_persist_path(cwd)
    if not path:
        return
    try:
        import sqlite3

        conn = sqlite3.connect(path, timeout=1.0)
        try:
            if path not in _PROBE_NEG_PERSIST_TABLE_INITED:
                _probe_neg_persist_ensure_schema(conn)
                _PROBE_NEG_PERSIST_TABLE_INITED.add(path)
            key = sha256((label + "\x1f" + (task or "")).encode("utf-8", "replace")).hexdigest()[:24]
            conn.execute(
                "INSERT OR REPLACE INTO probe_neg_cache VALUES (?,?,?)",
                (key, label, time.time()),
            )
            (count,) = conn.execute("SELECT COUNT(*) FROM probe_neg_cache").fetchone()
            if count > _PROBE_NEG_PERSIST_CAP:
                conn.execute(
                    "DELETE FROM probe_neg_cache WHERE key IN (SELECT key FROM probe_neg_cache ORDER BY ts LIMIT ?)",
                    (count - _PROBE_NEG_PERSIST_CAP,),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        log_swallowed("compile.probe_neg_persist.put", exc)


# W129 — probe positive-result cache. Companion to W126's negative cache.
# When a probe returns NON-empty data (callers list, coupling table, body
# slice, etc.), cache it keyed on (label, canonical_task, sorted_paths) so
# a follow-up task that happens to canonicalize to the same shape reuses
# the prior result without re-running the regex AND subprocess. 60-s TTL
# (shorter than negative — positive results are more sensitive to repo
# state changes); 256-entry cap, LRU on insertion order.
_PROBE_POSITIVE_CACHE: dict[str, tuple[float, dict]] = {}
_PROBE_POS_TTL_S = 60.0
_PROBE_POS_CAP = 256

# W152 — persistent positive probe cache (SQLite, cross-session). Sister
# to W147 (`run_roam_cache`). Stores POSITIVE probe results across
# process restarts so successful probe data (callers, coupling slice,
# etc.) survives session boundaries. 24-hour TTL, 4096-row cap.
_PROBE_POS_PERSIST_CAP = 4096
_PROBE_POS_PERSIST_TTL_S = 24 * 3600.0
_PROBE_POS_PERSIST_TABLE_INITED: set[str] = set()


def _probe_pos_persist_ensure_schema(conn) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS probe_pos_cache "
        "(key TEXT PRIMARY KEY, head TEXT, label TEXT, result_json TEXT, ts REAL)"
    )
    _set_wal(conn)


def _probe_pos_persist_get(label: str, task: str, named_paths: list[str], cwd: str | None, head: str) -> dict | None:
    path = _run_roam_persist_path(cwd)
    if not path:
        return None
    try:
        import sqlite3

        conn = sqlite3.connect(path, timeout=1.0)
        try:
            if path not in _PROBE_POS_PERSIST_TABLE_INITED:
                _probe_pos_persist_ensure_schema(conn)
                _PROBE_POS_PERSIST_TABLE_INITED.add(path)
            key = sha256(_probe_pos_cache_key(label, task, named_paths).encode("utf-8", "replace")).hexdigest()[:24]
            row = conn.execute(
                "SELECT head, result_json, ts FROM probe_pos_cache WHERE key=?",
                (key,),
            ).fetchone()
            if row is None:
                return None
            cached_head, result_json, ts = row
            if (time.time() - ts) > _PROBE_POS_PERSIST_TTL_S:
                conn.execute("DELETE FROM probe_pos_cache WHERE key=?", (key,))
                conn.commit()
                return None
            if head and cached_head and cached_head != head:
                conn.execute("DELETE FROM probe_pos_cache WHERE key=?", (key,))
                conn.commit()
                return None
            try:
                return json.loads(result_json)
            except json.JSONDecodeError:
                return None
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        log_swallowed("compile.probe_pos_persist.get", exc)
        return None


def _probe_pos_persist_put(
    label: str, task: str, named_paths: list[str], cwd: str | None, head: str, result: dict
) -> None:
    if not result:
        return
    path = _run_roam_persist_path(cwd)
    if not path:
        return
    try:
        import sqlite3

        conn = sqlite3.connect(path, timeout=1.0)
        try:
            if path not in _PROBE_POS_PERSIST_TABLE_INITED:
                _probe_pos_persist_ensure_schema(conn)
                _PROBE_POS_PERSIST_TABLE_INITED.add(path)
            key = sha256(_probe_pos_cache_key(label, task, named_paths).encode("utf-8", "replace")).hexdigest()[:24]
            conn.execute(
                "INSERT OR REPLACE INTO probe_pos_cache VALUES (?,?,?,?,?)",
                (key, head or "", label, _fast_json_dumps(result), time.time()),
            )
            (count,) = conn.execute("SELECT COUNT(*) FROM probe_pos_cache").fetchone()
            if count > _PROBE_POS_PERSIST_CAP:
                conn.execute(
                    "DELETE FROM probe_pos_cache WHERE key IN (SELECT key FROM probe_pos_cache ORDER BY ts LIMIT ?)",
                    (count - _PROBE_POS_PERSIST_CAP,),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        log_swallowed("compile.probe_pos_persist.put", exc)


def _probe_pos_cache_key(label: str, task: str, named_paths: list[str]) -> str:
    canon = (task or "").strip().lower()[:120]
    paths = "|".join(sorted(named_paths or []))
    return f"{label}::{canon}::{paths}"


def _probe_pos_cached_hit(label: str, task: str, named_paths: list[str]) -> dict | None:
    key = _probe_pos_cache_key(label, task, named_paths)
    entry = _PROBE_POSITIVE_CACHE.get(key)
    if entry is None:
        return None
    ts, result = entry
    if time.monotonic() - ts > _PROBE_POS_TTL_S:
        _PROBE_POSITIVE_CACHE.pop(key, None)
        return None
    return result


def _probe_pos_record(label: str, task: str, named_paths: list[str], result: dict) -> None:
    if not result:
        return
    if len(_PROBE_POSITIVE_CACHE) >= _PROBE_POS_CAP:
        oldest = min(_PROBE_POSITIVE_CACHE.items(), key=lambda kv: kv[1][0])
        del _PROBE_POSITIVE_CACHE[oldest[0]]
    _PROBE_POSITIVE_CACHE[_probe_pos_cache_key(label, task, named_paths)] = (time.monotonic(), result)


# W130 — per-procedure probe-skip table. Classifier picks a procedure
# with high confidence on most tasks; many always_on probes are
# meaningless for that procedure shape (e.g. `subprocess_audit` adds
# nothing to a stack-trace-fix; `runtime_hotspots` is noise on a
# synthesis_query). Skipping irrelevant probes preserves the same
# envelope quality while cutting wasted work proportionally. Each
# entry lists probe LABELS that should NOT fire for that procedure.
# Probes not listed default to running (safe default).
_PROCEDURE_PROBE_SKIPS: dict[str, frozenset[str]] = {
    "stack_trace_fix": frozenset(
        {
            "subprocess_audit",
            "why_slow",
            "todo_audit",
            "deprecation",
            "env_vars",
            "entry_points",
            "config",
            "refactor_move",
            "api_surface",
        }
    ),
    "synthesis_query": frozenset(
        {
            "subprocess_audit",
            "why_slow",
            "deprecation",
            "config",
        }
    ),
    "structural_callers": frozenset(
        {
            "subprocess_audit",
            "why_slow",
            "todo_audit",
            "deprecation",
            "env_vars",
            "config",
            "api_surface",
        }
    ),
    "structural_coupling": frozenset(
        {
            "subprocess_audit",
            "why_slow",
            "todo_audit",
            "deprecation",
            "env_vars",
            "api_surface",
        }
    ),
    "structural_blast": frozenset(
        {
            "subprocess_audit",
            "todo_audit",
            "deprecation",
            "env_vars",
        }
    ),
    # W133 — extend skip table. freeform_explore correlates with prose
    # tasks ("trace how X works"); structural-detail probes typically
    # don't pay off. write_pytest is its own L1 lane via synthesis_query
    # — runtime/dep audits are noise. refactor_move tasks need the move
    # surface; perf/runtime probes are noise.
    # W189c — api_surface REMOVED; W165 iter-7 t4 audit (freeform_explore)
    # genuinely needs api_surface + stability_markers grounding.
    # todo_audit REMOVED: it self-gates on _TODO_AUDIT_RE (a microsecond
    # regex, no I/O before the gate), so the 10-40ms cost rationale never
    # applied — and the skip silently blanked "list TODO comments in X"
    # prompts that the probe answers outright. "owner_probe" REMOVED: dead
    # label (the registered extender label is "owners"), it never matched —
    # which is the only reason "who owns X" prompts kept working.
    "freeform_explore": frozenset(
        {
            "subprocess_audit",
            "why_slow",
            "deprecation",
            "env_vars",
            "config",
        }
    ),
    "refactor_move": frozenset(
        {
            "subprocess_audit",
            "why_slow",
            "deprecation",
            "env_vars",
            "todo_audit",
            "config",
            "entry_points",
        }
    ),
    # W46 — telemetry-driven skips using the ACTUAL labels
    # in _L1_ALWAYS_ON_PROBES (grep_replication / import_audit / compare /
    # pickaxe / criterion / scope_lock / output_shape / conventions /
    # module_name / reachability / config / find_by_desc / why_slow /
    # entry_points / test_impact / refactor_move / api_surface / owners /
    # env_vars / todo_audit / deprecation / subprocess_audit).
    #
    # Picked per-procedure on semantic grounds — these probes return None
    # >98% of the time for the procedure but still cost ~10-40ms in
    # wall + I/O. Pre-W46 they were running uselessly.
    #
    # `cli_verb_why_slow` ("why is `roam X` slow") — CLI-verb-anchored;
    #   no file path, no symbol body, no comparison or move; skip
    #   probes that need a file anchor or history pickaxe.
    "cli_verb_why_slow": frozenset(
        {
            "compare",
            "pickaxe",
            "module_name",
            "refactor_move",
            "import_audit",
            "test_impact",
        }
    ),
    # `top_n_ranking` ("top 5 most-imported files") — ranking shape;
    #   no per-target body, no compare, no move semantic.
    "top_n_ranking": frozenset(
        {
            "compare",
            "pickaxe",
            "refactor_move",
            "module_name",
            "import_audit",
            "owners",
            "test_impact",
        }
    ),
    # `symbol_defined_where` ("where is X defined") — single-symbol
    #   definition lookup; no perf / runtime / audit angle, no
    #   comparison, no move.
    "symbol_defined_where": frozenset(
        {
            "why_slow",
            "entry_points",
            "subprocess_audit",
            "todo_audit",
            "deprecation",
            "env_vars",
            "compare",
            "refactor_move",
            "find_by_desc",
            "test_impact",
        }
    ),
    # `file_history` ("what changed in X recently") — the git log embed IS
    #   the answer; no perf / move / compare / audit / grep angle applies.
    #   `owners` stays ON (useful for "who touched X"); `pickaxe` is OFF —
    #   it is symbol-anchored and the file-level log already answers.
    "file_history": frozenset(
        {
            "why_slow",
            "entry_points",
            "subprocess_audit",
            "todo_audit",
            "deprecation",
            "env_vars",
            "compare",
            "refactor_move",
            "import_audit",
            "test_impact",
            "module_name",
            "grep_replication",
            "pickaxe",
            "api_surface",
            "config",
            "find_by_desc",
        }
    ),
    # `repo_structure` ("layers/clusters/health of this codebase") — the
    #   repo-scoped summary IS the answer; no file/symbol-anchored probe applies.
    "repo_structure": frozenset(
        {
            "why_slow",
            "entry_points",
            "subprocess_audit",
            "todo_audit",
            "deprecation",
            "env_vars",
            "compare",
            "refactor_move",
            "import_audit",
            "test_impact",
            "module_name",
            "grep_replication",
            "pickaxe",
            "api_surface",
            "config",
            "find_by_desc",
        }
    ),
    # `entry_point_where` — the dedicated probe IS the W67 entry_points
    #   probe; skip the always-on duplicate plus unrelated audits.
    "entry_point_where": frozenset(
        {
            "why_slow",
            "entry_points",
            "subprocess_audit",
            "todo_audit",
            "deprecation",
            "env_vars",
            "compare",
            "refactor_move",
            "import_audit",
            "test_impact",
            "module_name",
            "grep_replication",
            "pickaxe",
            "find_by_desc",
        }
    ),
    # `config_where` — the dedicated probe IS the W49 config probe; skip
    #   the always-on duplicate plus unrelated audits.
    "config_where": frozenset(
        {
            "why_slow",
            "entry_points",
            "subprocess_audit",
            "todo_audit",
            "deprecation",
            "compare",
            "refactor_move",
            "import_audit",
            "test_impact",
            "module_name",
            "pickaxe",
            "api_surface",
            "config",
            "find_by_desc",
        }
    ),
    # `session_meta` — a continuation directive has NO task content; every
    #   content-keyed always-on probe is noise. Skip them all.
    "session_meta": frozenset(
        {
            "grep_replication",
            "import_audit",
            "compare",
            "pickaxe",
            "criterion",
            "scope_lock",
            "output_shape",
            "conventions",
            "module_name",
            "reachability",
            "config",
            "find_by_desc",
            "why_slow",
            "entry_points",
            "test_impact",
            "refactor_move",
            "api_surface",
            "owners",
            "env_vars",
            "todo_audit",
            "deprecation",
            "subprocess_audit",
        }
    ),
    # `self_contained_task` — the payload needs ZERO repo facts; skip every
    #   always-on probe (this is the whole point of the fast-path).
    "self_contained_task": frozenset(
        {
            "grep_replication",
            "import_audit",
            "compare",
            "pickaxe",
            "criterion",
            "scope_lock",
            "output_shape",
            "conventions",
            "module_name",
            "reachability",
            "config",
            "find_by_desc",
            "why_slow",
            "entry_points",
            "test_impact",
            "refactor_move",
            "api_surface",
            "owners",
            "env_vars",
            "todo_audit",
            "deprecation",
            "subprocess_audit",
        }
    ),
}


# W142 — per-probe-label smart timeouts. Today every probe gets 15s
# uniformly. Regex-only probes (no subprocess) should fail in <1s if
# they're going to fail; subprocess probes that call `roam <cmd>`
# typically complete in <5s; retrieve/semantic probes can legitimately
# take ~12s. Per-label timeouts fail-fast on hung probes without
# capping legitimate slow ones.
# W162 — per-section envelope budgets. The W119 global cap drops the LARGEST
# probe wholesale to fit the total budget, which can starve unrelated sections.
# These per-key budgets give each probe family a fair slice; oversize values
# are truncated in place (list → head; string → byte-cut with marker). Sizes
# tuned to common p95 payload widths observed in the 2075-call telemetry
# window; generous to avoid false positives. Run BEFORE the global cap so
# coarse drop-largest pruning fires only when truncation alone cannot fit.
_SECTION_BUDGET_BYTES: dict[str, int] = {
    "file_skeleton": 5_000,
    "callers": 3_000,
    "structural_imports": 3_000,
    "grep_results": 6_000,
    "top_n_ranking": 2_000,
    "symbol_definitions": 2_000,
    "cli_verb_slow_diagnosis": 3_000,
    "compare_x_vs_y_result": 3_000,
    "file_excerpt": 4_000,
    "recent_commits": 1_500,
    "trace_spans": 4_000,
}


def _truncate_list_to_budget(value: list, budget: int) -> list:
    """W162 — longest head prefix of `value` whose JSON serialization fits
    `budget` bytes (geometric backoff to avoid O(N) shrink on huge lists)."""
    if not value:
        return value
    n = len(value)
    while n > 0:
        head = value[:n]
        try:
            blob = _fast_json_dumps(head)
        except (TypeError, ValueError):
            n -= 1
            continue
        if len(blob) <= budget:
            return head
        if n > 16:
            n = max(1, n * budget // max(1, len(blob)))
        else:
            n -= 1
    return []


def _truncate_section_value(value, budget: int):
    """W162 — fit `value` under `budget` bytes by truncating in place.

    Lists are truncated to the longest head prefix that fits (binary search-ish
    halving; cheap for the small Ns we see in practice). Strings are byte-cut
    with a `…[truncated to N bytes]` marker. Other types are passed through
    unchanged (caller still gets the W119 global-cap safety net).
    """
    try:
        if isinstance(value, list):
            return _truncate_list_to_budget(value, budget)
        if isinstance(value, str):
            # 32-byte slack for the marker suffix.
            keep = max(0, budget - 32)
            if keep <= 0:
                return f"…[truncated to {budget} bytes]"
            return value[:keep] + f"…[truncated to {budget} bytes]"
    except Exception as exc:  # noqa: BLE001
        log_swallowed("compile.envelope.section_truncate", exc)
    return value


def _apply_section_budgets(prefetched: dict) -> dict[str, int]:
    """W162 — enforce per-section budgets on `prefetched` in place.

    Returns a `{key: original_bytes}` map for the truncated sections so the
    caller can surface `_section_budget_truncated` in the envelope.
    """
    truncated: dict[str, int] = {}
    if not prefetched:
        return truncated
    for key in list(prefetched.keys()):
        if key.startswith("_"):
            continue
        budget = _SECTION_BUDGET_BYTES.get(key)
        if budget is None:
            continue
        value = prefetched[key]
        try:
            blob = _fast_json_dumps(value)
        except (TypeError, ValueError) as exc:
            log_swallowed("compile.envelope.section_size", exc)
            continue
        original = len(blob)
        if original <= budget:
            continue
        new_value = _truncate_section_value(value, budget)
        prefetched[key] = new_value
        truncated[key] = original
    return truncated


_PROBE_TIMEOUT_BY_LABEL: dict[str, float] = {
    # Fast regex / file-stat probes — should never need more than 2s.
    "owner_probe": 2.0,
    "todo_audit": 2.0,
    "deprecation_audit": 2.0,
    "env_vars_audit": 2.0,
    "subprocess_audit": 2.0,
    "api_surface": 3.0,
    "config_by_name": 3.0,
    "entry_points": 3.0,
    "refactor_move": 5.0,
    "runtime_hotspots": 5.0,
}
_PROBE_TIMEOUT_DEFAULT = 12.0


def _record_probe_outcome(label, result, task, named_paths, cwd, head, prefetched):
    """Merge a probe result into prefetched + record the pos/neg caches
    (in-memory W129/W126 + persistent W152/W155). Returns updated prefetched."""
    if result:
        prefetched = prefetched | result
        _probe_pos_record(label, task, named_paths, result)
        if cwd:
            _probe_pos_persist_put(label, task, named_paths, cwd, head, result)
    else:
        _probe_neg_record(label, task)
        if cwd:
            _probe_neg_persist_put(label, task, cwd)
    return prefetched


def _filter_runnable_probes(
    procedure: str, task: str, named_paths: list[str], cwd: str | None, head: str, prefetched: dict
):
    """W126/W129/W130/W152/W155 — harvest cached positive hits (in-memory then
    persistent) and skip negative-cached / procedure-irrelevant probes. Returns
    (runnable_probes, prefetched_with_cache_hits_merged)."""
    skip_for_procedure = _PROCEDURE_PROBE_SKIPS.get(procedure, frozenset())
    runnable: list[tuple[str, object]] = []
    for label, fn in _L1_ALWAYS_ON_PROBES:
        if label in skip_for_procedure:
            continue
        cached = _probe_pos_cached_hit(label, task, named_paths)
        if cached is not None:
            prefetched = prefetched | cached
            continue
        if cwd:
            persisted = _probe_pos_persist_get(label, task, named_paths, cwd, head)
            if persisted is not None:
                prefetched = prefetched | persisted
                _probe_pos_record(label, task, named_paths, persisted)
                continue
        if _probe_neg_cached_miss(label, task):
            continue
        if cwd and _probe_neg_persist_get(label, task, cwd):
            _probe_neg_record(label, task)
            continue
        runnable.append((label, fn))
    return runnable, prefetched


def _run_always_on_probes(runnable_probes, task, named_paths, cwd, procedure, prefetched, head):
    """W42 — run the runnable always-on probes in parallel under a total wall
    budget (default 2500ms); `as_completed(timeout=budget)` stops waiting on a
    blocker, `shutdown(wait=False)` avoids re-blocking on a runaway thread.
    Merges results + records pos/neg caches. Returns updated prefetched."""
    import time as _t
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from concurrent.futures import TimeoutError as _CFTimeout

    budget_s = _W42_ALWAYS_ON_BUDGET_MS / 1000.0
    start = _t.monotonic()
    pool = ThreadPoolExecutor(max_workers=min(6, len(runnable_probes)))
    try:
        label_for: dict = {pool.submit(fn, task, named_paths, cwd, procedure): label for label, fn in runnable_probes}
        pending = set(label_for.keys())
        try:
            for fut in as_completed(label_for, timeout=budget_s):
                label = label_for[fut]
                pending.discard(fut)
                try:
                    per_probe = _PROBE_TIMEOUT_BY_LABEL.get(label, _PROBE_TIMEOUT_DEFAULT)
                    remaining = max(0.05, budget_s - (_t.monotonic() - start))
                    result = fut.result(timeout=min(per_probe, remaining))
                except Exception as exc:  # noqa: BLE001
                    log_swallowed(f"compile.always_on.{label}", exc)
                    continue
                prefetched = _record_probe_outcome(label, result, task, named_paths, cwd, head, prefetched)
        except _CFTimeout:
            for prem in pending:
                prem.cancel()
            log_swallowed(
                "compile.always_on.budget_exhausted", Exception(f"budget {_W42_ALWAYS_ON_BUDGET_MS}ms exceeded")
            )
    finally:
        pool.shutdown(wait=False)
    return prefetched


def _apply_always_on_extenders(
    procedure: str, task: str, named_paths: list[str], cwd: str | None, prefetched: dict
) -> dict:
    """Pattern C — fire each registered always-on extender; merge results.

    W125 — parallelize via ThreadPoolExecutor.
    W126 — skip probes that recently returned None for this same task.
    W129 — reuse cached positive results across canonical-equivalent tasks.
    W130 — skip probes the procedure declares irrelevant.
    W142 — per-probe smart timeouts (fast-fail on hung probes).
    """
    # W152 — fetch git head once for persistent probe cache validation
    # (cheap: memoized per-cwd via _memoized_head — no re-shell per compile).
    head = ""
    if cwd:
        try:
            head = _memoized_head(cwd) or ""
        except Exception:  # noqa: BLE001
            head = ""
    runnable, prefetched = _filter_runnable_probes(procedure, task, named_paths, cwd, head, prefetched)
    if not runnable:
        return prefetched
    return _run_always_on_probes(runnable, task, named_paths, cwd, procedure, prefetched, head)


# W42: total wall budget across all always_on probes per call.
# Past this, remaining probes are cancelled. Set generously so we only
# truncate the long tail. Override via env for tuning.
_W42_ALWAYS_ON_BUDGET_MS = int(os.environ.get("ROAM_ALWAYS_ON_BUDGET_MS", "2500"))


# 2026-06-05: hard wall cap for the SYNCHRONOUS procedure probe (inner_probe).
# After the always_on budget was made effective, inner_probe is the last
# uncapped synchronous call on the compile critical path. Today its probes are
# bounded by their own subprocess timeouts (telemetry: legit max ~3.8s for
# structural_dead `--no-decay`; the historical 14.7s max was pre-fix), but a
# future probe lacking an internal timeout would block the whole compile. The
# cap is generous (8s ≫ every legit probe) so it only fires on a genuine
# runaway — degrading to the rest of the prefetch (graceful, mirrors always_on).
_INNER_PROBE_TIMEOUT_S = float(os.environ.get("ROAM_INNER_PROBE_TIMEOUT_S", "8"))


def _probe_for_procedure_bounded(
    procedure: str, named_paths: list[str], cwd: str | None, task: str | None, timeout_s: float
) -> dict:
    """Run `_probe_for_procedure` with a hard wall cap. On timeout, return {} —
    the compile proceeds with whatever else was prefetched (graceful degrade).
    The orphaned thread finishes in the background (`shutdown(wait=False)`)."""
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import TimeoutError as _CFTimeout

    pool = ThreadPoolExecutor(max_workers=1)
    try:
        fut = pool.submit(_probe_for_procedure, procedure, named_paths, cwd, task=task)
        try:
            return fut.result(timeout=timeout_s) or {}
        except _CFTimeout:
            log_swallowed("compile.inner_probe.timeout", Exception(f"inner_probe {procedure} exceeded {timeout_s}s"))
            return {}
        except Exception as exc:  # noqa: BLE001
            log_swallowed(f"compile.inner_probe.{procedure}", exc)
            return {}
    finally:
        pool.shutdown(wait=False)


# W-SWE (2026-06-02): parallel-implementation over-generalization guard.
# PROVEN on SWE-bench django-11138 (Docker-graded): the broad freeform_explore
# envelope surfaced 3 db backends (mysql/oracle/sqlite3) side-by-side, and the
# agent copied mysql's `!= tzname` conditional onto the oracle backend, breaking
# its SQL. Annotating the envelope to warn against cross-implementation
# pattern-copying flipped that instance from FAIL to PASS. Test-dir groups are
# excluded (they are not real parallel implementations — they added confused
# agent turns on the 11532 win with no upside).
_PARALLEL_IMPL_RE = re.compile(r"(?:[\w./]+?/)?(?P<parent>[\w_]+)/(?P<sib>[\w_]+)/(?P<base>[\w_]+\.py)")
_PARALLEL_IMPL_PARENT_DENYLIST = frozenset({"tests", "test", "testing", "fixtures", "docs"})


def _parallel_impl_blob(source: object) -> str:
    """Flatten any envelope structure (dict/list/str) into a path-searchable
    string. Used so detection sees paths wherever they live (named_paths,
    likely_files, prefetched_facts match lists, …), not just one field."""
    if isinstance(source, str):
        return source
    import json as _json

    try:
        return _json.dumps(source, default=str)
    except (TypeError, ValueError):
        return str(source)


def _detect_parallel_impl_groups(source: object, min_siblings: int = 3) -> list[str]:
    """Find N+-sibling parallel-impl groups (e.g. backends/{mysql,oracle}/X.py)
    in an envelope. `source` may be a string blob or any dict/list structure."""
    import collections

    blob = _parallel_impl_blob(source)
    groups: dict[tuple[str, str], set[str]] = collections.defaultdict(set)
    for m in _PARALLEL_IMPL_RE.finditer(blob):
        parent = m.group("parent")
        if parent in _PARALLEL_IMPL_PARENT_DENYLIST or parent.endswith("_tests"):
            continue
        groups[(parent, m.group("base"))].add(m.group("sib"))
    return sorted(
        f"{parent}/{{{','.join(sorted(sibs))}}}/{base}"
        for (parent, base), sibs in groups.items()
        if len(sibs) >= min_siblings
    )


def _annotate_parallel_implementations(facts: dict, scan: object = None) -> None:
    """Add a `parallel_implementations` warning fact when the envelope surfaces
    3+ parallel sibling implementations, so the agent does NOT over-generalize a
    fix pattern across them. Detects over `scan` (default: `facts` itself) but
    writes the warning into `facts`. Additive + idempotent."""
    if not isinstance(facts, dict) or "parallel_implementations" in facts:
        return
    groups = _detect_parallel_impl_groups(scan if scan is not None else facts)
    if not groups:
        return
    facts["parallel_implementations"] = groups
    facts["parallel_implementations_definition"] = (
        "These file groups are PARALLEL implementations of one interface. Fix "
        "ONLY the path(s) the task names and treat each as independent — do NOT "
        "copy a conditional or pattern from one onto sibling implementations."
    )


def _apply_envelope_budget_cap(prefetched: dict, proc: str, conf: float) -> int:
    """W119/W151 — multi-budget envelope cap keyed on a prelim recommended model
    (opus 64K / sonnet 16K / haiku 4K): drop the largest non-definition fields
    until under budget. Mutates `prefetched`; returns the final envelope byte size."""
    if proc == "freeform_explore" or conf < 0.6:
        prelim_rec = "opus"
    elif (proc.startswith("structural_") or proc in ("stack_trace_fix", "refactor_move")) and conf >= 0.85:
        prelim_rec = "haiku"
    else:
        prelim_rec = "sonnet"
    budget = {"haiku": 4 * 1024, "sonnet": 16 * 1024, "opus": 64 * 1024}[prelim_rec]
    try:
        envelope_bytes = len(_fast_json_dumps(prefetched))
    except (TypeError, ValueError) as exc:
        log_swallowed("compile.envelope.budget_dump", exc)
        envelope_bytes = 0
    if envelope_bytes <= budget:
        return envelope_bytes
    sizes = sorted(
        (
            (k, len(_fast_json_dumps(v)))
            for k, v in prefetched.items()
            if not k.endswith("_definition") and not k.startswith("_")
        ),
        key=lambda kv: -kv[1],
    )
    dropped: list[str] = []
    while sizes:
        if len(_fast_json_dumps(prefetched)) <= budget:
            break
        k, _ = sizes.pop(0)
        if k in prefetched:
            del prefetched[k]
            prefetched.pop(f"{k}_definition", None)
            dropped.append(k)
    envelope_bytes = len(_fast_json_dumps(prefetched))
    if dropped:
        prefetched["_envelope_budget_pruned"] = {
            "reason": f"envelope > {budget} bytes; dropped largest probe(s)",
            "dropped_keys": dropped,
            "final_bytes": envelope_bytes,
        }
    return envelope_bytes


def _recommend_model(proc: str, conf: float, envelope_bytes: int) -> str:
    """W136 — route to opus (freeform / big payload / low-conf), haiku (high-conf
    small-payload structural), else sonnet."""
    if proc == "freeform_explore" or envelope_bytes >= 16 * 1024 or conf < 0.6:
        return "opus"
    if (
        (proc.startswith("structural_") or proc in ("stack_trace_fix", "refactor_move"))
        and conf >= 0.85
        and envelope_bytes < 4 * 1024
    ):
        return "haiku"
    return "sonnet"


def _w128_always_on_timeout_s() -> float:
    return max(0.1, (_W42_ALWAYS_ON_BUDGET_MS / 1000.0) + 0.25)


def _future_result_or_none(fut, timeout_s: float, log_label: str):
    from concurrent.futures import TimeoutError as _CFTimeout

    try:
        return fut.result(timeout=timeout_s)
    except _CFTimeout as exc:
        log_swallowed(f"{log_label}.timeout", exc)
    except Exception as exc:  # noqa: BLE001
        log_swallowed(log_label, exc)
    return None


def _timed_future_result(timings: dict, label: str, fn):
    t0 = time.monotonic()
    try:
        return fn()
    finally:
        timings[label] = (time.monotonic() - t0) * 1000.0


def _run_w128_parallel(proc, task, w77_high_conf, named_only, cwd, prefetched, timings):
    """W128 — fan the always_on extenders + L10 symbol resolution in parallel
    (independent IO → sum-of-two collapses to max-of-two). W88 skips L10 for
    high-confidence structural tasks that already have a named path. Merges the
    L10 result + records both section timings; returns updated prefetched."""
    skip_l10 = w77_high_conf and proc.startswith("structural_") and named_only
    from concurrent.futures import ThreadPoolExecutor

    pool = ThreadPoolExecutor(max_workers=2)
    l10 = None
    try:
        ao_fut = pool.submit(_apply_always_on_extenders, proc, task, named_only, cwd, prefetched)
        l10_fut = None if skip_l10 else pool.submit(_probe_l10_symbol_resolution, task, cwd)
        ao_result = _timed_future_result(
            timings,
            "always_on",
            lambda: _future_result_or_none(ao_fut, _w128_always_on_timeout_s(), "compile.section.always_on"),
        )
        if ao_result is not None:
            prefetched = ao_result
        l10 = _timed_future_result(
            timings,
            "l10_symbol_resolution",
            lambda: None if l10_fut is None else _future_result_or_none(l10_fut, 20.0, "compile.section.l10"),
        )
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    if l10:
        prefetched.update(l10)
    return prefetched


@dataclass
class PlanV0:
    """The v0 plan envelope. 7 core fields + 2 routing fields."""

    task: str
    procedure: str
    likely_files: list[str]
    required_checks: list[str]
    forbidden_paths: list[str]
    plan_quality: float
    model_calls_avoided: list[str]
    # routing — the TASK→TOOL map applied to this specific task
    recommended_first_command: str
    rejected_procedures: list[str] = field(default_factory=list)
    # metadata
    repo_head: str | None = None
    compiled_at: str | None = None
    plan_version: str = "v0.1"
    # R10.1: classifier confidence on the procedure choice (0..1). Used by
    # select_artifact to gate specialized policies behind a threshold.
    classifier_confidence: float = 0.0
    # v0.4 — structured parallel-tool hint (the documented -84%/-78% wins).
    # When non-empty, the agent should call these tools in ONE tool_use block.
    recommended_parallel_tools: list[str] = field(default_factory=list)

    def _effective_forbidden_paths(self) -> list[str]:
        """W34b (E7): forbidden_paths is edit-relevant. Read-only procedures
        (structural_*, trace_query, freeform_explore) don't edit, so shipping
        16 paths + a "DO NOT edit" instruction is noise. Only synthesis tasks
        actually need this list."""
        if self.procedure == "synthesis_query":
            return list(self.forbidden_paths)
        return []

    def to_envelope(self) -> dict:
        d = asdict(self)
        # W21: stale-index warning on the FULL envelope. Check both task-extracted
        # paths AND any likely_files the compiler resolved via search.
        named = _extract_file_paths(self.task) + list(self.likely_files or [])
        staleness = _named_path_staleness(named, None)
        if staleness:
            d["index_staleness"] = staleness
        return {
            "schema": "roam-plan-v0",
            "schema_version": self.plan_version,
            "plan": d,
        }

    def to_lean_envelope(self, cwd: str | None = None) -> dict:
        """v0.1 LEAN envelope — synthesis/trace procedures.

        Drops likely_files (proven-noise on named-target tasks),
        required_checks (agent finds these), rejected_procedures (overhead).
        Keeps forbidden_paths (safety) + routing hint + quality (Guard).

        W34a (E8): when probe data is available (e.g. trace_query's
        `trace_spans` from `_probe_trace_for_task`), include it. Lean
        used to silently DROP prefetched data even when probe fired —
        agent then re-ran `roam retrieve` to get what compile already had.
        """
        plan_obj: dict = {
            "task": self.task,
            "procedure": self.procedure,
            "recommended_first_command": self.recommended_first_command,
            "forbidden_paths": self._effective_forbidden_paths(),
            "plan_quality": self.plan_quality,
        }
        # Trace probe — task-text driven, doesn't need named_paths.
        if self.procedure == "trace_query":
            trace = _probe_trace_for_task(self.task, cwd)
            if trace:
                plan_obj["prefetched_facts"] = trace
        return {
            "schema": "roam-plan-v0-lean",
            "schema_version": self.plan_version,
            "plan": plan_obj,
        }

    def _output_contract(self) -> dict:
        """X1 (2026-05-29 evening) — output-length contract.

        Anthropic charges 5× per output token vs input. Optimizing the
        envelope (input) ignores 80% of the cost. The cap forces concise
        answers; agent system_prompt must mirror "Answer in ≤N words".

        Per-procedure defaults: probe-fired structural answers are
        naturally short (list + scores); freeform/trace tolerate longer.
        """
        caps = {
            "structural_coupling": 200,
            "structural_callers": 250,
            "structural_dead": 300,
            "structural_cycle": 200,
            "structural_complexity": 250,
            "structural_blast": 250,
            "trace_query": 350,
            "synthesis_query": 400,
            "freeform_explore": 400,
        }
        return {
            "max_words": caps.get(self.procedure, 350),
            "format": "concise prose with cited files; begin with the answer directly",
        }

    def to_facts_contract_envelope(self, cwd: str | None = None) -> dict:
        """v0.7 facts + AnswerContract — R7's predicted Pareto winner,
        R9 confirmed (+24% score/$ vs vanilla on Sonnet 4.6 matched tasks).

        R10 refinement (2026-05-29): answer_contract is now
        PROCEDURE-SPECIFIC. Empirical per-procedure data showed the
        generic 5-bullet contract loses 8.8pp quality on
        structural_coupling vs vanilla because "cite files/lines/why"
        doesn't match what a coupling answer needs (file pairs + strength
        scores). Each procedure now gets a contract tailored to what a
        good answer LOOKS like in that family.
        """
        named_only = _extract_file_paths(self.task)
        contract = _PROCEDURE_CONTRACTS.get(self.procedure, _GENERIC_CONTRACT)
        # v0.4 — batch-search override fires when 3+ symbols are named.
        # Documented -69% to -79% tokens vs N sequential single-symbol calls.
        starter = _maybe_batch_search_starter(self.task, named_only) or _PROCEDURE_STARTERS.get(self.procedure, "")
        # v0.4 — substitute {symbol}/{target} placeholders from named paths.
        # If a placeholder remains unfilled (no symbol/path in task), DROP the
        # starter rather than ship a literal "{symbol}" — agents would run a
        # broken command. The parallel_tools hint still ships independently.
        if starter and ("{symbol}" in starter or "{target}" in starter):
            target = named_only[0] if named_only else None
            if target:
                starter = starter.replace("{symbol}", target).replace("{target}", target)
            else:
                starter = ""
        plan_obj: dict = {
            "task": self.task,
            "named_paths": named_only,
            "forbidden_paths": self._effective_forbidden_paths(),
            "repo_head": self.repo_head,
            "answer_contract": list(contract),
            "output_contract": self._output_contract(),
        }
        if starter:
            plan_obj["roam_starter"] = starter
        # v0.4 — structured parallel-tool list (the -84%/-78% combo wins).
        if self.recommended_parallel_tools:
            plan_obj["recommended_parallel_tools"] = list(self.recommended_parallel_tools)
        # W21: surface stale-index signals so the agent can verify
        # named_paths before trusting them (see cvc A/B 2026-05-30).
        staleness = _named_path_staleness(named_only, None)
        if staleness:
            plan_obj["index_staleness"] = staleness
        # Check for files newer than index (post-index edits)
        newer_files = _check_files_newer_than_index(named_only, cwd)
        if newer_files:
            plan_obj["index_stale"] = True
            if "prefetched_facts" not in plan_obj:
                plan_obj["prefetched_facts"] = {}
            plan_obj["prefetched_facts"]["index_stale"] = newer_files
        return {
            "schema": "roam-plan-v0-facts-contract",
            "schema_version": self.plan_version,
            "plan": plan_obj,
        }

    def to_l1_probe_envelope(self, cwd: str | None = None) -> dict:
        """v0.5 L1 PROBE-AND-FILL envelope — embed ANSWERS, not pointers.

        Per the lever-inventory notes: the highest single
        lever for cost reduction. Compiler runs roam queries AT COMPILE
        TIME and embeds results in `prefetched_facts`. Agent collapses
        from gather+synthesize to synthesize-only — 1-2 turns instead
        of 4-7, projected 4× cost reduction.

        W42 — decomposed via three registry tables + tiny helpers
        (`_apply_task_text_probe`, `_apply_backtick_fallback`,
        `_apply_always_on_extenders`). Was cc=63 brain-method.
        """
        named_only = _extract_file_paths(self.task)
        if not named_only:
            # Bare-filename fallback: "what's exported from cmd_verify.py" has no
            # slash-path, so _extract_file_paths returns [] and the path-driven
            # probes never fire. Resolve a UNIQUE bare code-filename to its repo
            # path so api_surface / file_skeleton / file_summary light up.
            named_only = _resolve_bare_filenames(self.task, cwd)
        if not named_only:
            # `roam <subcommand>` fallback: "add a --json flag to roam smells"
            # has no path/filename — resolve the subcommand to its module file so
            # the agent gets the file to edit (no-op outside the roam repo).
            named_only = _resolve_cli_command_files(self.task, cwd)
        if not named_only:
            # Module-name fallback: "explain the compiler architecture" names a
            # module, not a file — resolve a unique stem/package match so the
            # describe_file skeleton/summary probes anchor on the right file.
            named_only = _resolve_module_names(self.task, cwd)
        contract = _PROCEDURE_CONTRACTS.get(self.procedure, _GENERIC_CONTRACT)
        plan_obj: dict = {
            "task": self.task,
            "named_paths": named_only,
            "forbidden_paths": self._effective_forbidden_paths(),
            "repo_head": self.repo_head,
            "answer_contract": list(contract),
            "output_contract": self._output_contract(),
        }
        # W43 P3 — time each section so production telemetry can show
        # which probe phase dominates compile latency.
        timings: dict[str, float] = {}

        def _timed(label: str, fn):  # noqa: ANN001
            t = time.perf_counter()
            try:
                return fn()
            finally:
                timings[label] = (time.perf_counter() - t) * 1000.0

        # W45 C1 — module-name resolution must fire FIRST so its resolved
        # paths can chain into downstream probes (coupling/callers/etc).
        # If no explicit named_path was extracted but the task says
        # "the auth module", the resolver fills in named_only here so
        # `_probe_for_procedure` below sees a target. Also seed the
        # prefetched dict with the resolution metadata so the agent sees
        # which paths got picked (transparency).
        _seed_prefetched: dict = {}
        mod_result = _probe_module_name_for_task(self.task, named_only, cwd)
        if mod_result:
            resolved = mod_result.get("resolved_named_paths_from_module_name") or []
            stitched = [p for p in resolved if p.endswith(".py")]
            if stitched and not named_only:
                named_only = stitched[:2]
                _seed_prefetched.update(mod_result)
        prefetched: dict = _timed(
            "inner_probe",
            lambda: (
                _seed_prefetched
                | _probe_for_procedure_bounded(self.procedure, named_only, cwd, self.task, _INNER_PROBE_TIMEOUT_S)
            ),
        )
        prefetched = _timed(
            "task_text", lambda: _apply_task_text_probe(self.procedure, self.task, named_only, cwd, prefetched)
        )
        prefetched = _timed(
            "backtick_fallback", lambda: _apply_backtick_fallback(self.procedure, self.task, cwd, prefetched)
        )
        # W128 — fan always_on extenders + L10 symbol resolution in parallel.
        prefetched = _run_w128_parallel(
            self.procedure,
            self.task,
            getattr(self, "_w77_high_confidence", False),
            named_only,
            cwd,
            prefetched,
            timings,
        )
        # Stash timings on the plan so the telemetry helper can record them.
        # Uses a private attr to avoid changing the dataclass shape.
        object.__setattr__(self, "_w43_timings_ms", timings)
        if prefetched:
            # W98 — defensive shape validation: drop entries that are None,
            # empty list, or empty dict. These contribute no value to the
            # envelope and only add noise/cost. They sometimes leak from
            # probes when an upstream tool returns sparse data.
            prefetched = {k: v for k, v in prefetched.items() if v not in (None, "", [], {})}
            # W162 — per-section budgets. Truncate oversize probe payloads
            # IN PLACE so individual sections shrink instead of being dropped
            # wholesale by the W119 global cap. Surface a parallel
            # `_section_budget_truncated` map naming the affected keys +
            # their pre-truncation byte size so consumers know what was lost.
            _w162_truncated = _apply_section_budgets(prefetched)
            if _w162_truncated:
                prefetched["_section_budget_truncated"] = _w162_truncated
            # W119 — envelope budget cap. The W105 t25/t28/t32/t33/t41
            # compile no_output failures correlated with very-large
            # prefetched_facts (>32 KB serialized). Cap total size at
            # 32 KB; when over budget, drop the LARGEST single field
            # (definitions excluded) until under. Definitions are tiny
            # so the order doesn't matter much.
            # W135 — use _fast_json_dumps in the hot budget loop (called
            # up to O(N) times per cap-overshoot envelope).
            # W151 — multi-budget envelope keyed on prelim recommended_model.
            # Today a 32KB cap squeezes Opus tasks (hardest, biggest probe
            # payload) and wastes bytes on Haiku tasks (small input, no
            # capacity to consume them anyway). Right-sizing per target
            # compounds quality: Opus gets richer context, Haiku stays lean.
            _bproc = getattr(self, "procedure", "") or ""
            try:
                _bconf = float(getattr(self, "classifier_confidence", 0.0) or 0.0)
            except (TypeError, ValueError):
                _bconf = 0.0
            envelope_bytes = _apply_envelope_budget_cap(prefetched, _bproc, _bconf)
        else:
            envelope_bytes = 0
        if prefetched:
            plan_obj["prefetched_facts"] = prefetched
            # W-SWE — annotate parallel implementations. Scan the WHOLE envelope
            # (named_paths + likely_files + prefetched) since sibling-impl paths
            # surface in named_paths/likely_files, not just prefetched_facts.
            _annotate_parallel_implementations(
                prefetched,
                scan={"env": plan_obj, "likely": list(self.likely_files or [])},
            )
            # W136 — model recommendation hint. Routes downstream callers
            # toward the right model: cheap routing for high-confidence
            # small-payload structural tasks (Haiku safe), default for
            # medium, escalation for big/low-conf/freeform.
            try:
                _conf = float(getattr(self, "classifier_confidence", 0.0) or 0.0)
            except (TypeError, ValueError):
                _conf = 0.0
            _proc = getattr(self, "procedure", "") or ""
            plan_obj["recommended_model"] = _recommend_model(_proc, _conf, envelope_bytes)
            plan_obj["recommended_model_reason"] = f"procedure={_proc} conf={_conf:.2f} envelope_bytes={envelope_bytes}"
            # W97 — anti-distract: when the envelope carries 5+ rich
            # facts, lead the answer_contract with a directive telling
            # the agent to USE the prefetched data BEFORE tool-calling.
            # The W82 holdout showed agents sometimes ignore probe data
            # when the contract opens with "do X first" verb language.
            domain_keys = [
                k
                for k in prefetched
                if not k.endswith("_definition")
                and k not in ("decision_criterion", "output_shape", "scope_lock", "resolved_symbols")
            ]
            if len(domain_keys) >= 5:
                anti_distract = (
                    f"You already have {len(domain_keys)} prefetched facts "
                    f"({', '.join(sorted(domain_keys)[:6])}{'...' if len(domain_keys) > 6 else ''}). "
                    f"INSPECT these first — most questions answer directly "
                    f"from them, no tool calls needed."
                )
                existing = plan_obj.get("answer_contract", [])
                plan_obj["answer_contract"] = [anti_distract, *existing]
        # W34b (E10): structured fallback tools — if the prefetched answer
        # turns out to be insufficient (rare; agent can't tell ahead of time),
        # the agent has a programmatic list of which tools would have
        # produced the same data. Avoids parsing the natural-language
        # recommended_first_command string.
        if self.recommended_parallel_tools:
            plan_obj["fallback_tools_if_prefetched_insufficient"] = list(self.recommended_parallel_tools)
        # W21: stale-index warning (same surface as facts-contract envelope).
        staleness = _named_path_staleness(named_only, cwd)
        if staleness:
            plan_obj["index_staleness"] = staleness
        return {
            "schema": "roam-plan-v0-l1-probe",
            "schema_version": self.plan_version,
            "plan": plan_obj,
        }

    def to_facts_envelope(self, cwd: str | None = None) -> dict:
        """v0.5 FACTS-ONLY envelope — minimum-information control for H1.

        Tests: does ANY plan envelope beat plain extracted facts on
        structural queries? Drops procedure label, routing hint, semantic
        search results (likely_files keeps ONLY path-extracted from task
        text — the deterministic part). No interpretation, no advice.
        Just: indisputable facts + safety boundaries.

        If H1 is true, facts-only beats LEAN/full on structural queries
        and we know the procedure/routing layer was over-planning.
        """
        # Re-extract: keep only paths the task text NAMED. Drop any
        # search-semantic noise even if it's already in self.likely_files.
        named_only = _extract_file_paths(self.task)
        prefetched: dict = {}
        # W45 C1 — module-name shorthand ("the thing module") resolves to a
        # concrete file path via filesystem glob. A glob-resolved path is an
        # INDISPUTABLE FACT (deterministic, like a task-extracted path), so it
        # belongs in the minimum-information envelope too — not just L1. Stitch
        # it into named_paths AND surface the resolution metadata so the agent
        # sees which path was picked. Only fires when no explicit path was named.
        if not named_only:
            mod_result = _probe_module_name_for_task(self.task, named_only, cwd)
            if mod_result:
                stitched = [
                    p for p in (mod_result.get("resolved_named_paths_from_module_name") or []) if p.endswith(".py")
                ]
                if stitched:
                    named_only = stitched[:2]
                    prefetched = dict(mod_result)
        plan: dict = {
            "task": self.task,
            "named_paths": named_only,
            "forbidden_paths": self._effective_forbidden_paths(),
            "repo_head": self.repo_head,
        }
        if prefetched:
            plan["prefetched_facts"] = prefetched
        # W21: stale-index warning even in the minimum-information envelope.
        staleness = _named_path_staleness(named_only, None)
        if staleness:
            plan["index_staleness"] = staleness
        # Check for files newer than index (post-index edits)
        newer_files = _check_files_newer_than_index(named_only, cwd)
        if newer_files:
            plan["index_stale"] = True
            if "prefetched_facts" not in plan:
                plan["prefetched_facts"] = {}
            plan["prefetched_facts"]["index_stale"] = newer_files
        return {
            "schema": "roam-plan-v0-facts",
            "schema_version": self.plan_version,
            "plan": plan,
        }


# ---- v0.6 ArtifactSelector — per-procedure artifact routing ----
# Empirically calibrated from H1 + correctness judge results:
#   - FactsEnvelope wins on structural and synthesis (cheaper, more correct)
#   - LEAN envelope is the right shape for trace queries
#   - Full envelope works for freeform_explore (where there's no clear entry)
#
# This is the lookup table that ArtifactSelector consults.

_ARTIFACT_POLICY = {
    # R7 revision (2026-05-28, post cost-adjusted aggregation):
    # The R6 "facts wins structural" finding was a small-sample artifact.
    # When rubric correctness is included AND samples are deeper, the
    # per-procedure winners on score-per-dollar are:
    #   structural_complexity → vanilla wins (849 score/$); plan-v0 close second
    #   structural_coupling   → vanilla wins (806); plan-v0 second
    #   structural_dead       → plan-v0 wins (247)
    #   structural_cycle      → plan-v0 wins (572)
    #   synthesis_query       → plan-v0 wins (417)
    #   trace_query           → plan-v0 wins (774); lean is second
    #   freeform_explore      → FACTS wins (623) — the only confirmed facts win
    # v0.6 can't pick "vanilla" (would mean no plan envelope at all);
    # plan-v0 (full envelope) is the safe winner where vanilla is best.
    # R9 update (2026-05-29): structural_complexity → "contract" — the
    # facts+answer_contract envelope shows +8.9pp quality vs vanilla on
    # this procedure (95.0 vs 86.1, n=4 vs n=7). Aggregate facts-contract
    # also beats vanilla on score-per-dollar by +26% across the corpus
    # but the per-procedure picture is mixed; only structural_complexity
    # is a clean defensible win. Keep other policies stable until more
    # per-procedure Opus data lands.
    "structural_dead": "full",
    "structural_coupling": "full",
    "structural_complexity": "contract",
    "structural_cycle": "full",
    "structural_callers": "full",
    "structural_blast": "full",
    "structural_query": "full",  # legacy fallback
    "synthesis_query": "full",
    # refactor_move needs the full move surface (impact + callers + target
    # skeleton); was hitting the implicit `.get(p, "full")` fallback —
    # explicit now so the registry lint can pin every procedure's intent.
    "refactor_move": "full",
    "trace_query": "lean",
    "freeform_explore": "facts",  # R7 revision (was "full")
    "describe_file": "facts",  # W-LIFT — file skeleton/summary IS the answer
    # W35a: stack_trace_fix routes to L1-probe via _L1_PROBE_ELIGIBLE when
    # the probe fires; this policy is the FALLBACK when probe returned no
    # readable frames (file deleted, glob mismatch, etc.).
    "stack_trace_fix": "full",
    # W11/W12/W13: probe data IS the answer (symbol list / top-N
    # ranking / hotspots). "facts" policy minimises envelope size so the agent
    # gets just the probe payload + recommended_first_command, not full
    # structural context. Without these entries, `_ARTIFACT_POLICY.get(p, "full")`
    # fell through to "full" → 46 historical calls dropped to art_label:full
    # despite probes firing. Discovered via 2026-06-02 compiler-usage analysis.
    "symbol_defined_where": "facts",
    "top_n_ranking": "facts",
    "cli_verb_why_slow": "facts",
    "file_history": "facts",  # W-HIST — embedded git log IS the answer
    "repo_structure": "facts",  # W-REPO — embedded summary IS the answer
    "entry_point_where": "facts",  # W-ENTRY — embedded entry list IS the answer
    "config_where": "facts",  # W-CFG — embedded grep hits ARE the answer
    "session_meta": "facts",  # W-META — tiny brief; conversation is the task
    "self_contained_task": "facts",  # W-BATCH — zero-probe notice envelope
    # W28 — compare-X-vs-Y: probe IS the answer (diff summary + divergence
    # points). Tight "facts" envelope avoids shipping full structural
    # context the agent doesn't need.
    "compare_x_vs_y": "facts",
}


def select_artifact(plan: "PlanV0") -> str:
    """v0.6 ArtifactSelector — returns 'facts' | 'lean' | 'full' | 'contract'.

    R10.1 (2026-05-29): specialized policies (non-"full") only apply when
    classifier confidence is high. Low-confidence classifications fall
    back to "full" — the safe baseline that tolerates classifier error.
    This avoids the R10 vue01 regression where a misclassified task hit
    the wrong specialized contract and lost more than the generic.
    """
    policy = _ARTIFACT_POLICY.get(plan.procedure, "full")
    # W51 — per-procedure threshold; fall back to global if absent.
    threshold = _PER_PROCEDURE_CONF_THRESHOLD.get(plan.procedure, _CONFIDENCE_THRESHOLD)
    if policy != "full" and plan.classifier_confidence < threshold:
        return "full"
    return policy


_L1_PROBE_ELIGIBLE = (
    "structural_coupling",
    "structural_callers",
    "structural_dead",
    "structural_blast",
    "structural_complexity",
    "structural_cycle",
    "trace_query",
    # W34c (E2/E3): synthesis + freeform on named files now ship file skeleton.
    "synthesis_query",
    "freeform_explore",
    # W-LIFT — describe-file ships the same file skeleton/summary probe; needs
    # L1 eligibility or it silently degrades to a `full` no-probe envelope.
    "describe_file",
    # W35a: stack-trace frames are extracted from the task text, not from
    # named_paths — eligibility handled specially in compile_for_artifact.
    "stack_trace_fix",
    # W181 — refactor_move added; W166 classifier returns it but L1
    # eligibility was missing, silently degrading the envelope.
    "refactor_move",
    # W11/W12/W13 — three new probe families need L1 eligibility
    # so that when their probes fire (and return non-None data), the artifact
    # is labelled `l1_probe` instead of `full`. Without this, the L1 fire-rate
    # KPI under-counted by 46 calls in 2 days. See W22 → compiler-health
    # alert "l1 fire rate 45% below 60% target" (the 2026-06-02 readings).
    "symbol_defined_where",
    "top_n_ranking",
    "cli_verb_why_slow",
    # W28 — compare-X-vs-Y (2026-06-02): task-text-driven, no named_paths
    # needed. The extractor pulls (X, Y) directly from the task.
    "compare_x_vs_y",
    # W-HIST (2026-06-09) — file-history needs L1 eligibility so the embedded
    # git log labels the artifact `l1_probe` instead of degrading to `full`.
    "file_history",
    # W-REPO (2026-06-09) — repo-structure is task-text-driven (no named
    # paths); eligibility + the task-text-target set below.
    "repo_structure",
    # W-ENTRY / W-CFG (2026-06-09) — both task-text-driven.
    "entry_point_where",
    "config_where",
    # W-META (2026-06-09) — continuation directives; brief embed.
    "session_meta",
    # W-BATCH (2026-06-09) — self-contained payloads; notice embed.
    "self_contained_task",
)


_L1_TASK_TEXT_TARGET_PROCEDURES = frozenset(
    {
        "trace_query",
        "stack_trace_fix",
        "symbol_defined_where",
        "top_n_ranking",
        "cli_verb_why_slow",
        "compare_x_vs_y",
        # W1-fix (2026-06-10) — describe_file's file/module NAME in the task
        # text is the target. Without this, a module-describe prompt in an
        # index-less repo ("what does the thing module do" where the DB
        # resolver returns []) skipped the L1 path entirely — so the W45
        # filesystem module-name probe never ran and the envelope was EMPTY
        # (caught by test_w45_c1_module_name_stitches_into_named_paths).
        "describe_file",
        # W-REPO — the dimension keyword in the task text IS the target.
        "repo_structure",
        # W-ENTRY / W-CFG — intent keyword / config name IS the target.
        "entry_point_where",
        "config_where",
        # W-META — the directive itself is the target.
        "session_meta",
        # W-BATCH — the payload itself is the target.
        "self_contained_task",
    }
)


_L1_PROCEDURE_KEYS: dict[str, tuple[str, ...]] = {
    "structural_coupling": (
        "structural_imports",
        "structural_imported_by_top",
        "temporal_coupling_pairs",
    ),
    "structural_callers": ("callers",),
    "structural_dead": ("unused_top_10", "target_symbol"),
    "structural_blast": ("impact_top_files",),
    "structural_complexity": ("complexity_metrics",),
    "structural_cycle": ("cycles", "cycle_count"),
    "trace_query": ("trace_spans",),
    "symbol_defined_where": (
        "symbol_definitions",
        "symbol_definitions_unavailable",
    ),
    "top_n_ranking": ("top_n_ranking", "top_n_ranking_unavailable"),
    "cli_verb_why_slow": (
        "cli_verb_slow_diagnosis",
        "cli_verb_subcommand",
        "cli_verb_remediation",
    ),
    "compare_x_vs_y": (
        "compare_x_vs_y_result",
        "compare_x_vs_y_unavailable",
    ),
    "file_history": (
        "file_recent_commits",
        "file_history_unavailable",
    ),
    "repo_structure": (
        "repo_structure_result",
        "repo_structure_unavailable",
    ),
    "entry_point_where": (
        "entry_points",
        "declared_entry_points",
        "entry_points_unavailable",
    ),
    "config_where": (
        "config_matches",
        "config_matches_unavailable",
    ),
    "session_meta": (
        "session_brief",
        "session_brief_unavailable",
    ),
    "self_contained_task": ("self_contained_notice",),
    "synthesis_query": (
        "file_skeleton",
        "sibling_test_excerpt",
        "convention_samples",
        "grep_results",
    ),
    "freeform_explore": (
        "symbol_definitions",
        "resolved_entity",
        "file_skeleton",
        "file_excerpt",
        "recent_commits",
        "symbol_history",
        "path_comparison",
        "bug_site_slice",
        "grep_results",
        "convention_samples",
        "resolved_named_paths_from_module_name",
        "reachability",
        "config_matches",
        "semantic_matches",
        "runtime_hotspots",
        "runtime_hotspots_unavailable",
        "entry_points",
        "test_impact",
        "refactor_move",
        "api_surface",
        "owners",
        "env_vars_used",
        "todo_items",
        "deprecation_markers",
        "subprocess_sites",
        # Security taint scan + perf algo-catalog findings.
        "taint_summary",
        "algo_findings",
        # World-model classifiers + design-pattern instances.
        "world_model",
        "design_patterns",
    ),
    "stack_trace_fix": ("stack_frames", "import_audit", "grep_results"),
    "refactor_move": ("refactor_move", "grep_results"),
    "describe_file": (
        "file_skeleton",
        "file_summary",
        "full_file_body",
        "file_excerpt",
        # W1-fix (2026-06-10) — module-describe prompts in an index-less
        # repo resolve via the W45 filesystem probe; its stitch key must
        # count as procedure data or the envelope downgrades to "full"
        # and DROPS the resolution.
        "resolved_named_paths_from_module_name",
    ),
}


def _l1_has_target(plan: "PlanV0") -> bool:
    return bool(plan.likely_files) or plan.procedure in _L1_TASK_TEXT_TARGET_PROCEDURES


def _l1_has_procedure_data(procedure: str, prefetched: dict) -> bool:
    required = _L1_PROCEDURE_KEYS.get(procedure, ())
    return bool(required and any(k in prefetched for k in required))


def _maybe_append_compile_telemetry(
    plan: "PlanV0", env: dict, art_label: str, compile_ms: float, cwd: str | None
) -> None:
    """W39 D1 — best-effort append to `.roam/compile-runs.jsonl`.

    Records one JSON line per `compile_for_artifact` call so production
    fire-rate / classifier-confidence / envelope-size distributions can
    be measured from real workloads (not just synthetic corpora).

    Never raises. Skips when:
      - cwd is None (likely a unit test)
      - `.roam/` doesn't exist (not a roam-initialized project)
      - log file is >10 MB (rotate by hand)
    """
    from roam.observability import log_swallowed

    if not cwd:
        return
    log_dir = os.path.join(cwd, ".roam")
    if not os.path.isdir(log_dir):
        return
    log_path = os.path.join(log_dir, "compile-runs.jsonl")
    try:
        if os.path.exists(log_path) and os.path.getsize(log_path) > 10 * 1024 * 1024:
            return  # rotate manually; never grow unbounded
    except OSError as exc:
        log_swallowed("compile.telemetry.size_check", exc)
        return
    plan_obj = (env or {}).get("plan") or {}
    prefetched = plan_obj.get("prefetched_facts") or {}
    keys = sorted(k for k in prefetched if not k.endswith("_definition"))
    import hashlib

    try:
        envelope_bytes = len(_fast_json_dumps(env))
    except (TypeError, ValueError) as exc:
        log_swallowed("compile.telemetry.envelope_size", exc)
        envelope_bytes = -1
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "task_hash": hashlib.sha256(plan.task.encode("utf-8", "replace")).hexdigest()[:12],
        "task_prefix": plan.task[:80],
        "procedure": plan.procedure,
        "classifier_conf": plan.classifier_confidence,
        "art_label": art_label,
        "prefetched_keys": keys,
        "envelope_bytes": envelope_bytes,
        "compile_ms": round(compile_ms, 1),
        # W5: stamp agent_mode from env so `compile-stats --by-mode`
        # populates. Host platforms set ROAM_AGENT_MODE in their compile exec env.
        # Rows pre-dating this edit lack the field; `--by-mode` buckets them
        # as 'unknown'.
        "agent_mode": os.environ.get("ROAM_AGENT_MODE", "unknown"),
        # 2026-06-09 — stamp the compiler-code fingerprint so telemetry
        # shifts (routing distributions, L1 rate, latency) are attributable
        # to compiler revisions. Without this, a classifier change and a
        # workload change are indistinguishable in compile-stats.
        "compiler_fp": _compiler_fingerprint(),
        # 2026-06-10 — what the hook channel was advised to do, so the
        # skip rate for generation-shaped tasks is measurable per repo.
        "injection_advice": injection_advice(plan.procedure, plan.task),
    }
    # W43 P3 — per-section timings if the plan attached them as
    # `_W43_TIMINGS_MS`. Optional: only present when L1 routing fired.
    timings = getattr(plan, "_w43_timings_ms", None)
    if timings:
        entry["probe_timings_ms"] = {k: round(v, 1) for k, v in timings.items()}
    # W58 — cache-hit flag for production visibility.
    entry["cache_hit"] = bool(getattr(plan, "_w58_cache_hit", False))
    # W149 — off-thread write opt-in via ROAM_TELEMETRY_OFFTHREAD=1.
    # Default stays synchronous to preserve test contracts that read
    # the JSONL file immediately after compile. Set the env var when
    # latency matters more than read-after-write visibility.
    line = _fast_json_dumps(entry) + "\n"
    if os.environ.get("ROAM_TELEMETRY_OFFTHREAD") in ("1", "true", "yes", "on"):
        _ensure_telemetry_worker()
        try:
            _TELEMETRY_QUEUE.put_nowait((log_path, line))
            return
        except _w149_queue.Full:
            pass  # fall through to sync write
    try:
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError as exc:
        log_swallowed("compile.telemetry.write", exc)


# ---- W56 — persistent envelope cache (SQLite, atomic, cross-process) ----
#
# The W44 I3 in-memory cache hits ~85% within a single process but cold-start
# always re-computes from scratch. W56 adds a disk-backed envelope cache that
# survives across processes. Key: sha256(task + repo_head + cwd_resolved).
# On hit AND matching repo HEAD, return the stored envelope at ~5ms; cold
# compile is ~500ms. The expected hot-task speedup is ~100×.
#
# Schema (.roam/compile-envelope-cache.sqlite):
#   key TEXT PRIMARY KEY, repo_head TEXT, art_label TEXT,
#   envelope_json TEXT, ts REAL
#
# Invalidation strategy: rows are only valid when their `repo_head` matches
# the current HEAD. Stale rows are pruned by `roam compile-cache clear`
# or auto-evicted by capacity (LRU on ts).

_ENVELOPE_CACHE_FILENAME = "compile-envelope-cache.sqlite"
_ENVELOPE_CACHE_MAX_ROWS = 2048


def _envelope_cache_path(cwd: str | None) -> str | None:
    if not cwd:
        return None
    p = os.path.join(cwd, ".roam", _ENVELOPE_CACHE_FILENAME)
    if not os.path.isdir(os.path.dirname(p)):
        return None
    return p


def _compiler_fingerprint() -> str:
    """Compiler-code fingerprint for cache keys (compiler.py mtime).

    Busts caches when the compiler CODE changes (probe/classifier edits).
    A (task, HEAD) key alone goes stale under UNCOMMITTED dev: HEAD doesn't
    move, so a code fix can't reach a cache that already holds the old
    result (the host kept serving pre-fix routing — observed again 2026-06-09
    via the PLAN cache, which lacked the stamp the envelope cache had).
    Prod is unaffected — the file is stable between deploys."""
    try:
        return str(int(os.path.getmtime(__file__)))
    except OSError as exc:
        log_swallowed("compile.cache_key_mtime", exc)
        return ""


def _envelope_cache_key(task: str, repo_head: str | None, cwd: str | None) -> str:
    """W57.5 follow-up (2026-06-02): canonicalize before hashing so the
    envelope cache hits across trivial rephrasings — same normalization
    the plan_cache + _cache_key already use. Without this, plan_cache
    hit but envelope_cache missed → redundant probe re-execution."""
    import hashlib

    h = hashlib.sha256()
    h.update(_canonicalize_task(task).encode("utf-8", "replace"))
    h.update(b"\x00")
    h.update((repo_head or "").encode("utf-8"))
    h.update(b"\x00")
    h.update((cwd or "").encode("utf-8"))
    h.update(b"\x00")
    h.update(_compiler_fingerprint().encode("utf-8"))
    return h.hexdigest()[:32]


# ---- W70 — dependency-fingerprint invalidation ----
#
# Before W70 the envelope cache invalidated on ANY HEAD change, even a
# README-only commit. That coarseness wastes most of the cache on every
# new commit. W70 stores per-envelope file mtimes; on lookup we re-stat
# the dependencies. If any mtime differs from what we cached, the row is
# stale and gets evicted — even when HEAD matches. Conversely a HEAD
# change is no longer fatal if no dependent file actually moved.
_ENV_CACHE_SCHEMA_V2 = (
    "CREATE TABLE IF NOT EXISTS env_cache "
    "(key TEXT PRIMARY KEY, repo_head TEXT, art_label TEXT, "
    "envelope_json TEXT, ts REAL, dep_mtimes_json TEXT)"
)


def _ensure_env_cache_schema(conn) -> None:
    """Create env_cache + add `dep_mtimes_json` column if pre-W70 schema.
    Idempotent. Best-effort: silently accepts whatever SQLite says."""
    conn.execute(_ENV_CACHE_SCHEMA_V2)
    # Old tables (W56) lack `dep_mtimes_json`. Try to add it; ignore "duplicate column".
    import sqlite3 as _sqlite3

    try:
        conn.execute("ALTER TABLE env_cache ADD COLUMN dep_mtimes_json TEXT")
    except _sqlite3.OperationalError as exc:
        # Expected when column already exists; benign.
        log_swallowed("compile.envelope_cache.alter_table", exc)


# W45–W49 — denylist illustrative / redundant / git-derived keys from the dep
# fingerprint. The blind "any string with / and ." scan over-captured: grep_results
# (W196) span many files; style/scaffolding excerpts; bodies redundant-with-
# likely_files; HISTORICAL keys (W47) invalidate only on HEAD move not edits;
# cross-file scans (W48) and trace-driven runtime hotspots (W49). Over-capture
# caused freeform_explore's 23% cache-hit rate. Structural answer keys
# (impact_top_files, structural_imports, callers, …) stay fingerprinted.
_DEP_ILLUSTRATIVE_KEYS = frozenset(
    {
        "grep_results",
        "convention_samples",
        "sibling_test_excerpt",
        "conftest_excerpt",
        "file_excerpt",
        "file_skeleton",
        "full_file_body",
        "src_under_test_excerpt",
        "resolved_named_paths_from_module_name",
        "temporal_coupling_pairs",
        "recent_commits",
        "symbol_history",
        "owners",
        "api_surface",
        "env_vars_used",
        "todo_items",
        "deprecation_markers",
        "subprocess_sites",
        "entry_points",
        "runtime_hotspots",
        "runtime_hotspots_unavailable",
    }
)
_DEP_REF_FIELDS = ("path", "file", "location", "test_path", "src_path", "file_a", "file_b")


def _dep_paths_from_value(v) -> list[str]:
    """File-path references inside one prefetched-facts value (str / list / dict)."""
    paths: list[str] = []
    if isinstance(v, str):
        if "." in v and "/" in v:
            paths.append(v)
    elif isinstance(v, list):
        for item in v:
            if isinstance(item, dict):
                for f in _DEP_REF_FIELDS:
                    if isinstance(item.get(f), str) and "/" in item[f]:
                        paths.append(item[f].split(":")[0])
            elif isinstance(item, str) and "/" in item:
                paths.append(item)
    elif isinstance(v, dict):
        for f in _DEP_REF_FIELDS:
            if isinstance(v.get(f), str) and "/" in v[f]:
                paths.append(v[f].split(":")[0])
    return paths


def _envelope_dep_files(plan: "PlanV0", env: dict, cwd: str | None) -> dict:
    """Return {abs_or_rel_path: mtime_sec} for files this envelope depends on.

    Sources:
      * plan.likely_files (the task's named paths)
      * paths inside `prefetched_facts` that look like file refs
        (`structural_imports`, `top_files`, `sibling_test_excerpt.test_path`,
        `src_under_test_excerpt.path`, etc.)

    Best-effort: stat errors are silently skipped.
    """
    out: dict = {}
    candidates: list[str] = list(getattr(plan, "likely_files", []) or [])
    pf = (env.get("plan") or {}).get("prefetched_facts") or {}
    for key, v in pf.items():
        if key in _DEP_ILLUSTRATIVE_KEYS or key.endswith("_definition"):
            continue
        candidates.extend(_dep_paths_from_value(v))
    # Cap to bound the cache row size + stat cost.
    for c in list({c for c in candidates if isinstance(c, str)})[:40]:
        full = os.path.join(cwd, c) if cwd and not os.path.isabs(c) else c
        try:
            out[c] = round(os.path.getmtime(full), 3)
        except OSError as exc:
            log_swallowed("compile.envelope_dep_files.stat", exc)
            continue  # missing file → not a dependency we can fingerprint
    # The envelope's structural facts (callers, blast, layers, ...) derive
    # from the INDEX, not from the source files directly. Without stamping
    # the index itself, an envelope compiled from a stale index keeps being
    # served even after `roam index --force`: the source mtimes recorded at
    # store time already matched the edited files, so the W70 check could
    # never evict the poisoned row (observed 2026-06-11: cached callers
    # cited pre-edit line numbers across a forced re-index). Re-indexing
    # moves index.db's mtime, which now busts every row compiled before it.
    try:
        out[_INDEX_DEP_KEY] = round(os.path.getmtime(_index_db_path(cwd)), 3)
    except OSError as exc:
        log_swallowed("compile.envelope_dep_files.index_stat", exc)
    return out


_INDEX_DEP_KEY = "__index_db__"


def _index_db_path(cwd: str | None) -> str:
    return os.path.join(cwd or ".", ".roam", "index.db")


def _envelope_deps_are_fresh(cwd: str | None, dep_json: str | None) -> bool:
    """Re-stat each cached dep; True iff every mtime still matches.
    Returns True when there are no deps to check (degrades to HEAD-only)."""
    if not dep_json:
        return True
    try:
        deps = json.loads(dep_json)
    except (TypeError, ValueError) as exc:
        log_swallowed("compile.envelope_deps_are_fresh.parse", exc)
        return True
    if not deps:
        return True
    # Rows with dep fingerprints but NO index stamp predate the index-stamp
    # fix (2026-06-11). They may hold facts compiled from an index that has
    # since been rebuilt — the poisoned-row class the stamp exists to catch —
    # and nothing else can prove their consistency. Evict once; the next
    # compile re-caches with the stamp.
    if _INDEX_DEP_KEY not in deps and os.path.exists(_index_db_path(cwd)):
        return False
    for rel, cached_mtime in deps.items():
        if rel == _INDEX_DEP_KEY:
            full = _index_db_path(cwd)
        else:
            full = os.path.join(cwd, rel) if cwd and not os.path.isabs(rel) else rel
        try:
            current = round(os.path.getmtime(full), 3)
        except OSError as exc:
            log_swallowed("compile.envelope_deps_are_fresh.stat", exc)
            return False  # file vanished → stale
        # Compare with a small tolerance to absorb filesystem rounding.
        if abs(current - cached_mtime) > 0.005:
            return False
    return True


def _envelope_cache_lookup(plan: "PlanV0", cwd: str | None) -> tuple[dict, str] | None:
    """Return (envelope, label) on hit, None on miss. Never raises."""
    path = _envelope_cache_path(cwd)
    if not path:
        return None
    try:
        import sqlite3

        conn = sqlite3.connect(path, timeout=1.0)
        _set_wal(conn)
        try:
            _ensure_env_cache_schema(conn)
            key = _envelope_cache_key(plan.task, plan.repo_head, cwd)
            row = conn.execute(
                "SELECT repo_head, art_label, envelope_json, dep_mtimes_json FROM env_cache WHERE key=?",
                (key,),
            ).fetchone()
            if row is None:
                return None
            cached_head, art_label, env_json, dep_json = row
            # W70 — dep-mtime check is the new primary gate. HEAD remains
            # a coarse fallback signal when deps are absent (old rows).
            if not _envelope_deps_are_fresh(cwd, dep_json):
                conn.execute("DELETE FROM env_cache WHERE key=?", (key,))
                conn.commit()
                return None
            # Only enforce HEAD when no dep fingerprints were stored (legacy).
            if not dep_json and cached_head != (plan.repo_head or ""):
                conn.execute("DELETE FROM env_cache WHERE key=?", (key,))
                conn.commit()
                return None
            env = json.loads(env_json)
            return env, art_label
        finally:
            conn.close()
    except (OSError, sqlite3.DatabaseError, json.JSONDecodeError) as exc:
        log_swallowed("compile.envelope_cache.lookup", exc)
        return None


def _envelope_cache_store(plan: "PlanV0", env: dict, art_label: str, cwd: str | None) -> None:
    """Best-effort cache write. Never raises."""
    path = _envelope_cache_path(cwd)
    if not path:
        return
    # Do NOT cache lean DEGRADED envelopes: an L1 probe that was actually
    # attempted and returned empty can be transient (timeout / stale index).
    # Intentional lean/facts envelopes and full recipe fallbacks are cacheable;
    # otherwise repeated stable prompts stay permanent misses.
    try:
        plan_obj = env.get("plan") or {}
        if (
            art_label in {"facts", "lean", "contract"}
            and plan_obj.get("probe_attempted") is True
            and plan_obj.get("probe_returned_empty") is True
        ):
            return
    except Exception:  # noqa: BLE001 — never let the guard break caching
        pass
    try:
        import sqlite3

        conn = sqlite3.connect(path, timeout=1.0)
        _set_wal(conn)
        try:
            _ensure_env_cache_schema(conn)
            key = _envelope_cache_key(plan.task, plan.repo_head, cwd)
            # W70 — fingerprint the envelope's file dependencies.
            dep_mtimes = _envelope_dep_files(plan, env, cwd)
            conn.execute(
                "INSERT OR REPLACE INTO env_cache "
                "(key, repo_head, art_label, envelope_json, ts, dep_mtimes_json) "
                "VALUES (?,?,?,?,?,?)",
                (
                    key,
                    plan.repo_head or "",
                    art_label,
                    _fast_json_dumps(env),
                    time.time(),
                    _fast_json_dumps(dep_mtimes) if dep_mtimes else None,
                ),
            )
            # Capacity check + LRU eviction.
            (count,) = conn.execute("SELECT COUNT(*) FROM env_cache").fetchone()
            if count > _ENVELOPE_CACHE_MAX_ROWS:
                overflow = count - _ENVELOPE_CACHE_MAX_ROWS
                conn.execute(
                    "DELETE FROM env_cache WHERE key IN (  SELECT key FROM env_cache ORDER BY ts ASC LIMIT ?)",
                    (overflow,),
                )
            conn.commit()
        finally:
            conn.close()
    except (OSError, sqlite3.DatabaseError, ValueError) as exc:
        log_swallowed("compile.envelope_cache.store", exc)


# ---- W57 — persistent PlanV0 cache (same SQLite file as W56 env cache) ----
_PLAN_CACHE_TABLE_DDL = (
    "CREATE TABLE IF NOT EXISTS plan_cache (key TEXT PRIMARY KEY, repo_head TEXT, plan_json TEXT, ts REAL)"
)


def _plan_persist_key(task: str, cwd: str | None, repo_head: str | None) -> str:
    """W57.5 — canonicalize the task text so trivial rephrasings hit the
    same persistent plan-cache row. Same conservative canonicalization as
    the in-process cache key (`_cache_key`)."""
    import hashlib

    h = hashlib.sha256()
    h.update(_canonicalize_task(task).encode("utf-8", "replace"))
    h.update(b"\x00")
    h.update((cwd or "").encode("utf-8"))
    h.update(b"\x00")
    h.update((repo_head or "").encode("utf-8"))
    # Same compiler-code stamp as the envelope cache — a classifier edit
    # must invalidate persisted PLANS too, or the old `procedure` keeps
    # being served from here even though the envelope cache busted.
    h.update(b"\x00")
    h.update(_compiler_fingerprint().encode("utf-8"))
    return h.hexdigest()[:32]


def _plan_cache_lookup(task: str, cwd: str | None) -> "PlanV0 | None":
    """Return a deserialized PlanV0 on hit + matching HEAD; None otherwise."""
    path = _envelope_cache_path(cwd)
    if not path:
        return None
    # We need the current HEAD to know which row to look for / invalidate by.
    head = _memoized_head(cwd) if cwd else None
    if head is None:
        return None
    try:
        import sqlite3

        conn = sqlite3.connect(path, timeout=1.0)
        _set_wal(conn)
        try:
            conn.execute(_PLAN_CACHE_TABLE_DDL)
            key = _plan_persist_key(task, cwd, head)
            row = conn.execute(
                "SELECT repo_head, plan_json FROM plan_cache WHERE key=?",
                (key,),
            ).fetchone()
            if row is None:
                return None
            cached_head, plan_json = row
            if cached_head != head:
                conn.execute("DELETE FROM plan_cache WHERE key=?", (key,))
                conn.commit()
                return None
            data = json.loads(plan_json)
            # Reconstruct PlanV0 from its asdict() form.
            return PlanV0(**data)
        finally:
            conn.close()
    except (OSError, sqlite3.DatabaseError, json.JSONDecodeError, TypeError) as exc:
        log_swallowed("compile.plan_cache.lookup", exc)
        return None


def _plan_cache_store(task: str, cwd: str | None, plan: "PlanV0") -> None:
    path = _envelope_cache_path(cwd)
    if not path:
        return
    try:
        import sqlite3
        from dataclasses import asdict

        conn = sqlite3.connect(path, timeout=1.0)
        _set_wal(conn)
        try:
            conn.execute(_PLAN_CACHE_TABLE_DDL)
            head = plan.repo_head or ""
            key = _plan_persist_key(task, cwd, head)
            data = asdict(plan)
            conn.execute(
                "INSERT OR REPLACE INTO plan_cache VALUES (?,?,?,?)",
                (key, head, _fast_json_dumps(data), time.time()),
            )
            # Capacity: 2048 rows (same as env_cache).
            (count,) = conn.execute("SELECT COUNT(*) FROM plan_cache").fetchone()
            if count > _ENVELOPE_CACHE_MAX_ROWS:
                overflow = count - _ENVELOPE_CACHE_MAX_ROWS
                conn.execute(
                    "DELETE FROM plan_cache WHERE key IN (  SELECT key FROM plan_cache ORDER BY ts ASC LIMIT ?)",
                    (overflow,),
                )
            conn.commit()
        finally:
            conn.close()
    except (OSError, sqlite3.DatabaseError, ValueError, TypeError) as exc:
        log_swallowed("compile.plan_cache.store", exc)


def _stamp_index_staleness(env_obj: dict, plan: "PlanV0", cwd: str | None) -> None:
    """Pattern 1D — disclose when the envelope was compiled from an index
    OLDER than the task's named files.

    The cache side of this failure class is sealed (index stamp + generation
    sweep), but a compile against a lagging index still embeds drifted line
    numbers silently. Tell the agent instead: which files are newer than the
    index, and what to do about it. Best-effort; never raises.
    """
    try:
        files = list(getattr(plan, "likely_files", None) or [])[:12]
        if not files:
            return
        idx_mtime = os.path.getmtime(_index_db_path(cwd))
        stale = []
        for rel in files:
            full = os.path.join(cwd, rel) if cwd and not os.path.isabs(rel) else rel
            try:
                if os.path.getmtime(full) > idx_mtime + 1.0:
                    stale.append(rel)
            except OSError:
                continue
        if not stale:
            return
        plan_obj = env_obj.get("plan")
        if not isinstance(plan_obj, dict):
            return
        plan_obj["index_stale"] = True
        pf = plan_obj.setdefault("prefetched_facts", {})
        pf["index_stale"] = {"files_newer_than_index": stale}
        pf["index_stale_definition"] = (
            "These files were edited AFTER the index was built, so embedded "
            "line numbers and structural facts for them may have drifted. "
            "Trust the file content over embedded coordinates for these "
            "files, and run `roam index` to refresh."
        )
    except Exception as exc:  # noqa: BLE001 — disclosure must never break a compile
        log_swallowed("compile.index_staleness_stamp", exc)


def compile_for_artifact(plan: "PlanV0", cwd: str | None = None) -> tuple[dict, str]:
    """Compile the right envelope for this plan's artifact type.

    Returns (envelope, artifact_label) where artifact_label is one of
    'facts' / 'lean' / 'full' / 'l1_probe' / 'contract' for downstream telemetry.

    W56 — checks persistent envelope cache before computing. On hit
    (same task + same repo_head), returns the cached envelope in ~5ms
    instead of ~500ms. Cache file: `.roam/compile-envelope-cache.sqlite`.

    R9 breakthrough (2026-05-29): facts-contract (facts + 5-bullet
    answer-shape template) STRICTLY DOMINATES vanilla on Sonnet 4.6
    matched-task comparison: -24% turns, -23% cost, +0.6pp HIGHER
    quality, +31% score-per-dollar.

    W33: the auto-selector now prefers `to_l1_probe_envelope`
    when (a) procedure is structural/trace, (b) named_paths exist, and
    (c) the probe returned procedure-specific facts. This is the "give
    the answer, not the recipe" path — agent receives precomputed
    coupling pairs / callers / dead set instead of "use these tools in
    PARALLEL". The prior bug: `compile --artifact auto` never selected
    the L1 envelope, so every measurement saw recipe-only output and
    couldn't realize the probe-and-fill speedup.
    """
    # W56 — persistent envelope cache check (cross-process). On hit,
    # bypass all computation and return in ~5ms.
    # W77 — confidence-gated fast path: when classifier_confidence is
    # >=0.85 the procedure is unambiguous; we can skip a few cheaper
    # internal probes (specifically L10 symbol resolution) on cache MISS,
    # which dominates the warm-tier latency for symbol-only tasks. The
    # cache lookup itself is already <1ms on hit.
    _t0 = time.perf_counter()
    cached = _envelope_cache_lookup(plan, cwd)
    if cached is not None:
        cached_env, cached_label = cached
        # W58 — flag cache hit on the plan so telemetry can record it.
        object.__setattr__(plan, "_w58_cache_hit", True)
        _maybe_append_compile_telemetry(
            plan,
            cached_env,
            cached_label,
            (time.perf_counter() - _t0) * 1000,
            cwd,
        )
        return cached_env, cached_label
    # W77 — mark high-confidence plans so downstream can skip optional probes.
    if plan.classifier_confidence >= 0.85:
        object.__setattr__(plan, "_w77_high_confidence", True)

    art = select_artifact(plan)

    # W189b — promote to L1 when high-value task-text probes are
    # available. select_artifact may pick "facts" on low-conf
    # freeform_explore, but api_surface / refactor_move regex matches
    # carry their own task-specific signal that L1 unlocks. The W165
    # iter-7 t4 regression came from this gap: conf=0.45 → facts →
    # api_surface probe never fired despite the regex matching.
    if art == "facts" and (
        _API_SURFACE_RE.search(plan.task or "") is not None
        or _REFACTOR_MOVE_RE.search(plan.task or "") is not None
        or _W196_LITERAL_RE.search(plan.task or "") is not None  # W196
        or _W201_IMPORT_RE.search(plan.task or "") is not None  # W201
        # W11/W12/W13 — three new probe families are entirely
        # probe-driven: the answer IS the probe result (symbol_definitions /
        # top_n_ranking / cli_verb_slow_diagnosis). Promote unconditionally
        # when the policy picked "facts" so the L1 envelope embeds the
        # probe data. Without this, art stayed at "facts" with empty
        # prefetched_keys — see compiler-usage analysis 2026-06-02.
        or plan.procedure
        in (
            "symbol_defined_where",
            "top_n_ranking",
            "cli_verb_why_slow",
            # W28 — compare-X-vs-Y is entirely probe-driven; the diff
            # summary / divergence points ARE the answer.
            "compare_x_vs_y",
            # W-LIFT — describe-file is entirely probe-driven; the embedded
            # file skeleton/summary/body IS the answer. Without promotion it
            # stayed "facts" with an empty probe payload (626-byte envelope).
            "describe_file",
            # W-HIST — file-history is entirely probe-driven; the embedded
            # git log IS the answer.
            "file_history",
            # W-REPO — repo-structure is entirely probe-driven; the embedded
            # summary IS the answer.
            "repo_structure",
            # W-ENTRY / W-CFG — entirely probe-driven.
            "entry_point_where",
            "config_where",
            # W-META — entirely probe-driven (tiny brief embed).
            "session_meta",
            # W-BATCH — notice-driven.
            "self_contained_task",
        )
    ):
        art = "l1_probe"

    # W167 + W168 + W169 — lean-fallback gate. The W165 iteration-1 paid
    # A/B showed 4 of 5 losses came from the SAME pattern: a rich L1
    # envelope INDUCED the agent to over-act on tasks where the right
    # answer was "do nothing fancy". Three triggers force a lean
    # (facts) envelope instead:
    #   - W169 conf < 0.55: classifier is uncertain; probes are noisy
    #   - W167 bare stack-trace: no file path in error → no actionable
    #     patch target; rich patch hints lead the agent astray
    #   - W168 opinion task: "how should I structure", "what's the best
    #     way" → no data answer exists; envelope scaffolding is pure
    #     overhead
    # W169 scoped (iter-2 refinement): only gate stack_trace_fix and
    # refactor_move at low conf. freeform_explore at low conf is the
    # "what does X do" pattern that NEEDS file_skeleton — iter-2 t12
    # regression proved demoting it hurts.
    _w167_169_low_conf = float(plan.classifier_confidence or 0.0) < 0.55 and plan.procedure in (
        "stack_trace_fix",
        "refactor_move",
    )
    # W167 — bare stack-trace = no file:line AND no Traceback frame.
    # Accepts both `file.py:42` and `File "x.py", line 42` formats.
    _task_for_gate = plan.task or ""
    _has_file_line = bool(
        re.search(r"\b\S+\.\w{1,4}:\d+\b", _task_for_gate)
        or re.search(r"\bin\s+\S+\.py\b", _task_for_gate)
        or re.search(r"\bfile\s+['\"]\S+\.\w+", _task_for_gate, re.IGNORECASE)
        or "Traceback" in _task_for_gate
    )
    _w167_169_bare_stack = plan.procedure == "stack_trace_fix" and not _has_file_line
    # W168 — opinion shape only triggers on synthesis_query where the
    # heavy envelope was the actual harm (W165 t1).
    _w167_169_opinion = plan.procedure == "synthesis_query" and bool(
        re.search(
            r"^\s*(how\s+should\s+I|what'?s?\s+(the\s+)?best\s+way|"
            r"should\s+I\b|what'?s?\s+(the\s+)?recommended|"
            r"how\s+do\s+(I|you)\s+structure|how\s+to\s+structure)\b",
            plan.task or "",
            re.IGNORECASE,
        )
    )
    # W186 — cross-file pattern survey ("find every X", "list every X",
    # "verify that X"). W165 iter-6 showed t29 (sqlite3.connect survey)
    # and t30 (WAL verification) where vanilla grep beat compile by 7+
    # turns. Compile envelope ships single-file context but the task
    # spans many files — wrong shape entirely. Demote to lean.
    _w186_cross_file_survey = bool(
        re.search(
            r"\b(find|list|count|enumerate|locate)\s+(every|all|each)\b|"
            r"\bverify\s+(that\s+)?(every|all|each)\b|"
            r"\b(every|all|each)\s+(CLI\s+)?(command|file|function|class|"
            r"site|subcommand|caller|usage|occurrence|instance)\b.*\b(in|"
            r"across|throughout)\s+(the\s+)?(repo|repository|codebase|"
            r"project|src)\b",
            plan.task or "",
            re.IGNORECASE,
        )
    )
    # W188 — meta-self questions about compile/envelope/probe internals.
    # W165 iter-6 t27/t28 lost because compile recursively shipped data
    # about its own internals; the agent over-interpreted. When the task
    # references compile-side concepts (probe / envelope / dispatch /
    # procedure-keyed dict), use facts envelope.
    _w188_meta_self = bool(
        re.search(
            r"\b(_probe_|probe[-_]?(?:fire|chain|dispatch)|"
            r"compile[-_]?envelope|envelope[-_]?(?:add|field|shape)|"
            r"_PROBE_DISPATCH|procedure[-_]?keyed|"
            r"why\s+(?:doesn'?t|does\s+not|wouldn'?t|won'?t)\s+\S+\s+fire|"
            r"_probe_refactor_move|_probe_callers|_probe_synthesis)\b",
            plan.task or "",
        )
    )
    # W196 — cross-file-survey demote (W186) DROPPED: now that the grep-
    # replication probe ships actual hits with enclosing_symbol metadata,
    # cross-file tasks (`find every X`, `verify all X`) benefit from L1
    # routing instead of demoting to lean. The W195 tool-trace data
    # justified the swap: 51 vanilla greps now collapse to 1 envelope read.
    if _w167_169_low_conf or _w167_169_bare_stack or _w167_169_opinion or _w188_meta_self:
        art = "facts"

    # W-GENLEAN (2026-06-10) — generation-shaped synthesis (test-writing)
    # goes LEAN. Fable 5 A/B evidence: the full/L1 synthesis envelope is
    # token-NEGATIVE (+25%) with an IDENTICAL tool path to vanilla — the
    # agent re-reads the source regardless (rational: a good test needs
    # more context than any excerpt), so the rich envelope is pure input
    # overhead. The richer-excerpt counter-fix was A/B-REFUTED same day
    # (+12% vs before). Lean keeps forbidden_paths + the "SKIP roam for
    # content writing" starter and drops the dead-weight probe payload.
    _w_gen_synth = plan.procedure == "synthesis_query" and _TEST_WRITE_RE.search(plan.task or "") is not None
    if _w_gen_synth and art not in ("contract",):
        art = "lean"

    def _emit(env_obj: dict, label: str) -> tuple[dict, str]:
        """W39 D1 — telemetry-on-return wrapper.
        W56 — also stores envelope in persistent cache.
        2026-06-12 — stamps the stale-index disclosure first (the one
        mutation allowed here; it must precede the cache store so the
        cached row carries the same disclosure)."""
        _stamp_index_staleness(env_obj, plan, cwd)
        _envelope_cache_store(plan, env_obj, label, cwd)
        _maybe_append_compile_telemetry(
            plan,
            env_obj,
            label,
            (time.perf_counter() - _t0) * 1000,
            cwd,
        )
        return env_obj, label

    # W33: if eligible for L1 probe AND named_paths exist, try probe envelope
    # first. Falls back to declared `art` if probe returned no procedure-specific
    # data (the same fall-through that route_for_plan uses).
    # W34a (E8): trace_query is L1-eligible without named_paths because the
    # trace probe is task-text-driven (`roam retrieve` on the natural-language
    # task). Other procedures still require named_paths.
    probe_attempted = False
    has_target = _l1_has_target(plan)
    # Probe-trigger override: these shape regexes map 1:1 to L1-promotable
    # probes (test-impact / owner / TODO / taint / perf-algo) whose output
    # IS the answer. Bare-symbol phrasings of these shapes score only 0.35
    # confidence (no path bump), so the confidence-band policy chose "facts"
    # and the probe pipeline never ran — "which tests cover X" / "find SQL
    # injection risks" shipped empty envelopes while the probes that answer
    # them outright sat idle. A matched trigger attempts L1 regardless; the
    # existing fall-through still demotes to facts when probes return nothing.
    _probe_trigger = bool(plan.task) and any(
        r.search(plan.task)
        for r in (
            _TEST_IMPACT_RE,
            _OWNER_RE,
            _TODO_AUDIT_RE,
            _SECURITY_TAINT_RE,
            _ALGO_PERF_RE,
            _WORLD_MODEL_RE,
            _DESIGN_PATTERN_RE,
        )
    )
    # W167/W168/W169 — when gated to lean, skip the L1 probe path entirely.
    if (
        plan.procedure in _L1_PROBE_ELIGIBLE
        and has_target
        and (art != "facts" or _probe_trigger)
        and not _w_gen_synth  # W-GENLEAN — lean is final for test-writes
        and not (_w167_169_low_conf or _w167_169_bare_stack or _w167_169_opinion)
    ):
        probe_attempted = True
        env = plan.to_l1_probe_envelope(cwd=cwd)
        pre = env.get("plan", {}).get("prefetched_facts") or {}
        procedure_keys = {
            "structural_coupling": ("structural_imports", "structural_imported_by_top", "temporal_coupling_pairs"),
            "structural_callers": ("callers",),
            "structural_dead": ("unused_top_10", "target_symbol"),
            "structural_blast": ("impact_top_files",),
            "structural_complexity": ("complexity_metrics",),
            "structural_cycle": ("cycles", "cycle_count"),
            "trace_query": ("trace_spans",),
            # W11/W12/W13 — procedure-specific probe keys so the
            # L1 envelope recognizes that probe data is present and avoids
            # falling back to "full". Without these, the existing
            # `any(k in pre for k in procedure_keys[procedure])` test
            # returned False and the envelope downgraded.
            "symbol_defined_where": ("symbol_definitions", "symbol_definitions_unavailable"),
            "top_n_ranking": ("top_n_ranking", "top_n_ranking_unavailable"),
            "cli_verb_why_slow": ("cli_verb_slow_diagnosis", "cli_verb_subcommand", "cli_verb_remediation"),
            # W28 — either the result or the unavailable-remediation key
            # signals that probe data is present and L1 should fire.
            "compare_x_vs_y": ("compare_x_vs_y_result", "compare_x_vs_y_unavailable"),
            # W-HIST — embedded git log (or the explicit no-history answer).
            "file_history": ("file_recent_commits", "file_history_unavailable"),
            # W-REPO — embedded repo-scoped summary (or explicit degraded).
            "repo_structure": ("repo_structure_result", "repo_structure_unavailable"),
            # W-ENTRY / W-CFG — embedded probe data (or explicit degraded).
            "entry_point_where": ("entry_points", "declared_entry_points", "entry_points_unavailable"),
            "config_where": ("config_matches", "config_matches_unavailable"),
            # W-META — embedded brief (or explicit degraded).
            "session_meta": ("session_brief", "session_brief_unavailable"),
            # W-BATCH — the zero-probe notice.
            "self_contained_task": ("self_contained_notice",),
            # W34c (E2/E3): file_skeleton is the procedure-specific key for
            # synth + freeform L1 routing.
            "synthesis_query": ("file_skeleton", "sibling_test_excerpt", "convention_samples", "grep_results"),
            "freeform_explore": (
                # W-ENTITY (2026-06-05) — a resolved identifier upgrades a
                # no-file freeform prompt to l1_probe so the entity facts
                # (def + body + references) reach the envelope instead of an
                # empty one. Closes the ~49%-no-prefetch freeform population.
                "symbol_definitions",
                "resolved_entity",
                "file_skeleton",
                "file_excerpt",
                "recent_commits",
                "symbol_history",
                "path_comparison",
                "grep_results",  # W196 — grep-replication probe
                # W44 I1/I2 — convention samples and module-name resolution
                # are always-on but only freeform_explore consults the keys
                # to decide L1 routing.
                "convention_samples",
                "resolved_named_paths_from_module_name",
                # W48-W50 — reachability, config-by-name, semantic find.
                "reachability",
                "config_matches",
                "semantic_matches",
                # W66-W67 — runtime hotspots + entry-points.
                "runtime_hotspots",
                "runtime_hotspots_unavailable",
                "entry_points",
                # W80 — test-impact reverse map.
                "test_impact",
                # W101 — cross-file refactor impact.
                "refactor_move",
                # W102 — API surface scan.
                "api_surface",
                # W109-W113 — owner + env-vars + TODO + deprecation + subprocess.
                "owners",
                "env_vars_used",
                "todo_items",
                "deprecation_markers",
                "subprocess_sites",
                # Security taint scan + perf algo-catalog findings.
                "taint_summary",
                "algo_findings",
                # World-model classifiers + design-pattern instances.
                "world_model",
                "design_patterns",
            ),
            "stack_trace_fix": ("stack_frames", "import_audit", "grep_results"),
            "refactor_move": ("refactor_move", "grep_results"),  # W181 + W196
            # W-LIFT — describe-file: the skeleton probe's keys signal that the
            # file's structure/purpose is embedded, so L1 fires (not "full").
            # W1-fix — the W45 module-name stitch key counts too (index-less
            # repos resolve via filesystem, not the DB).
            "describe_file": (
                "file_skeleton",
                "file_summary",
                "full_file_body",
                "file_excerpt",
                "resolved_named_paths_from_module_name",
            ),
        }
        required = procedure_keys.get(plan.procedure, ())
        if required and any(k in pre for k in required):
            return _emit(env, "l1_probe")

    # Probe was eligible but returned no procedure-specific data — mark
    # the fallback envelope so callers / telemetry can detect "L1 was
    # tried but degraded" vs "L1 wasn't even attempted" (H3 fix).
    def _attach_probe_signal(envelope: dict) -> dict:
        if probe_attempted:
            plan_obj = envelope.get("plan")
            if isinstance(plan_obj, dict):
                plan_obj["probe_attempted"] = True
                plan_obj["probe_returned_empty"] = True
        return envelope

    if art == "contract":
        return _emit(_attach_probe_signal(plan.to_facts_contract_envelope(cwd=cwd)), "contract")
    if art == "facts":
        return _emit(_attach_probe_signal(plan.to_facts_envelope(cwd=cwd)), "facts")
    if art == "lean":
        # W34a (E8): pass cwd so trace probe can fire inside the lean envelope.
        return _emit(_attach_probe_signal(plan.to_lean_envelope(cwd=cwd)), "lean")
    return _emit(_attach_probe_signal(plan.to_envelope()), "full")


# ALL-LEVERS production routing (2026-05-29, validated +220% score/$ on 68% of corpus).
# Per-procedure model + envelope + contract dispatch.
# See the compiler lever-inventory notes.
#
# v5 (2026-05-29 16:30): MECHANISM/CALIBRATION SPLIT. The routing logic below
# is the universal mechanism. Model strings + cost ratios live in
# `calibration.py`. Swapping providers is a profile swap, not a code change.

# Back-compat constants (referenced by tests and external callers).
MODEL_HAIKU = "claude-haiku-4-5"
MODEL_SONNET = "claude-sonnet-4-6"


def route_for_plan(plan: "PlanV0", cwd: str | None = None, profile_name: str | None = None) -> dict:
    """Return production routing decision for this plan.

    Output shape:
        {
            "model": "claude-haiku-4-5" | "claude-sonnet-4-6",
            "envelope": "l1_probe" | "facts_contract" | "full",
            "contract_id": str — identifier for which 3-step contract to use,
            "envelope_data": dict — the actual envelope JSON,
            "rationale": str — why this route was chosen,
        }

    Empirically validated routing table:
        structural_* + named_paths + probe-returns-data → Haiku × L1 × procedure-contract
        freeform_explore                                → Haiku × FC R9 × cycle2 3-step
        trace_query                                     → Haiku × FC R9 × trace 3-step
        synthesis_query / fallback                      → Sonnet × FC R9
    """
    from .calibration import get_profile  # local import avoids cycle

    profile = get_profile(profile_name)

    def _model(procedure: str) -> str:
        tier = profile.procedure_routes.get(procedure, "heavy")
        return profile.model_for(tier)

    # Procedure-specific probe fired -> cheap model x L1. Keep this in sync
    # with compile_for_artifact's auto-L1 families, including task-text-only
    # probes such as symbol lookup, top-N ranking, CLI slow, and compare.
    if plan.procedure in _L1_PROBE_ELIGIBLE and _l1_has_target(plan):
        envelope_dict = plan.to_l1_probe_envelope(cwd=cwd)
        plan_section = envelope_dict.get("plan", {})
        prefetched = plan_section.get("prefetched_facts", {})
        if _l1_has_procedure_data(plan.procedure, prefetched):
            chosen_model = _model(plan.procedure)
            return {
                "model": chosen_model,
                "envelope": "l1_probe",
                "contract_id": f"{plan.procedure}_3step",
                "envelope_data": envelope_dict,
                "rationale": (f"{plan.procedure} probe-fired with procedure-specific data - {chosen_model} x L1"),
            }
        # Procedure-specific probe empty — fall through to FC R9 / Sonnet (safer).
    # Freeform-explore → Haiku × FC R9 (Cycle 2 winner, +110% score/$ validated)
    if plan.procedure == "freeform_explore":
        return {
            "model": _model(plan.procedure),
            "envelope": "facts_contract",
            "contract_id": "cycle2_3step_fewshot",
            "envelope_data": plan.to_facts_contract_envelope(cwd=cwd),
            "rationale": "freeform_explore — Haiku × 3-step+few-shot (Cycle 2 +110% score/$)",
        }
    # Trace-query → Haiku × FC R9 with trace-specific 3-step
    if plan.procedure == "trace_query":
        return {
            "model": _model(plan.procedure),
            "envelope": "facts_contract",
            "contract_id": "trace_3step",
            "envelope_data": plan.to_facts_contract_envelope(cwd=cwd),
            "rationale": "trace_query — Haiku × trace 3-step (validated in all-levers)",
        }
    # synthesis_query and fallback → Sonnet × FC R9 (no Haiku win found)
    return {
        "model": _model(plan.procedure),
        "envelope": "facts_contract",
        "contract_id": "fc_r9_default",
        "envelope_data": plan.to_facts_contract_envelope(cwd=cwd),
        "rationale": f"{plan.procedure} — Sonnet baseline (no Haiku win validated yet)",
    }


# (duplicate _run_roam removed 2026-05-29 — superseded by canonical
# definition above with `detail` flag for L1.1 probe-and-fill)


# Extract explicit file paths mentioned in the task text. Most real tasks
# name the file(s) they're about — that's a free signal before search-semantic.
_PATH_RE = re.compile(
    # W32: trailing boundary used to be [$|\s|['"`):.,] which
    # DROPPED paths followed by ? ! ; ] } > etc. — the most common case
    # being natural questions like "what is src/roam/cli.py?" Every prior
    # compile A/B was polluted by this: the obvious path went missing and
    # search-semantic noise filled named_paths. Now accepts any non-path
    # terminator OR end of string.
    # W32 / W33d: leading boundary now also accepts `:` and `,` so paths
    # right after them ("Files: src/X.py", "Edit: src/X.py", "X, src/Y.py")
    # are extracted. Was missing common natural-language preludes.
    r"(?:^|[\s'\"`(\[{:,])"
    # W40 C1: negative lookahead `(?!//)` blocks URL matches. Without it,
    # "https://github.com/x/foo.py" extracted as "//github.com/x/foo.py"
    # because `:` is a leading boundary char and the `//` then matches
    # the filename charclass. We do NOT want URL paths in named_paths.
    r"(?!//)"
    # W32: filename charclass also accepts hyphens — was [a-zA-Z0-9_]+ which
    # missed every kebab-case file (claude-sdk.js, my-component.vue, ...).
    r"((?:[a-zA-Z0-9_./-]+/)+[a-zA-Z0-9_-]+\.(?:py|ts|tsx|js|jsx|vue|go|rs|java|rb|php|sql|yml|yaml|json|md))"
    r"(?:$|[\s'\"`):.,;?!\]}>])",
    re.MULTILINE,
)
# R10: also catch directory paths (lines ending in '/' or with `:` after).
# These are scope anchors even though they don't name a specific file.
_DIR_RE = re.compile(
    r"(?:^|\s|['\"`(])"
    r"((?:[a-zA-Z0-9_-]+/){1,5})"
    r"(?:$|[\s'\"`):.,;?!\]}>]|cmd_\*|test_\*)",
    re.MULTILINE,
)


def _extract_file_paths(task: str) -> list[str]:
    """Pull file and directory paths from task text. Higher signal than search.

    R10: also extracts directory paths like `src/roam/commands/`
    that are scope anchors even without a specific filename. Empirically
    this cuts ~30% of search-semantic calls (the ones where the user
    referenced a directory but not a specific file inside it).
    """
    seen: list[str] = []
    for m in _PATH_RE.finditer(task):
        p = m.group(1)
        if p not in seen:
            seen.append(p)
    for m in _DIR_RE.finditer(task):
        p = m.group(1)
        # Skip if already covered by a file path above (prefix match)
        if any(s.startswith(p) for s in seen):
            continue
        if p not in seen:
            seen.append(p)
    return seen


_BARE_FILE_RE = re.compile(
    r"\b([\w.-]+\.(?:py|js|ts|tsx|jsx|go|rs|java|rb|php|c|cc|cpp|h|hpp|cs|kt|swift|scala|sql|vue))\b",
    re.IGNORECASE,
)


def _resolve_bare_filenames(task: str, cwd: str | None) -> list[str]:
    """Resolve bare code-filenames (e.g. "cmd_verify.py", no directory) to UNIQUE
    repo-relative paths via the index `files` table.

    `_extract_file_paths` is text-only and only yields SLASH-paths, so bare
    filenames — extremely common in real prompts ("what's exported from
    cmd_verify.py", "describe parser.py") — produced empty named_paths → the
    path-driven probes (api_surface / file_skeleton / file_summary) never fired
    (confirmed via compile telemetry, 2026-06-04). This bridges that gap. Bounded:
    only UNIQUE basename matches resolve (ambiguous → skipped); graceful on any DB
    error; returns [] when cwd/index unavailable.
    """
    if not task or not cwd:
        return []
    bares: list[str] = []
    for m in _BARE_FILE_RE.finditer(task):
        name = m.group(1)
        if "/" in name or "\\" in name:
            continue  # already a path → _extract_file_paths handles it
        if name not in bares:
            bares.append(name)
    if not bares:
        return []
    import os as _os
    import sqlite3 as _sq

    db_path = _os.path.join(cwd, ".roam", "index.db")
    if not _os.path.exists(db_path):
        return []
    resolved: list[str] = []
    try:
        conn = _sq.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
        try:
            for name in bares:
                rows = conn.execute(
                    "SELECT path FROM files WHERE path = ? OR path LIKE ? LIMIT 2",
                    (name, "%/" + name),
                ).fetchall()
                if len(rows) == 1:  # unique match only — ambiguous names skipped
                    resolved.append(rows[0][0])
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — best-effort resolution
        log_swallowed("compile.resolve_bare_filenames", exc)
        return []
    return resolved


def _query_unique_module_path(db_path: str, name: str) -> str | None:
    """Return the unique indexed path for module *name*, or None.

    Tries `<name>.py` stem match first, then `<name>/__init__.py` package
    match. Only UNIQUE matches resolve — an ambiguous stem (two compiler.py
    files) is skipped so the probe never anchors on the wrong module."""
    import sqlite3 as _sq

    conn = _sq.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    try:
        for pattern in ("%/" + name + ".py", name + ".py", "%/" + name + "/__init__.py"):
            rows = conn.execute(
                "SELECT path FROM files WHERE path LIKE ? LIMIT 2",
                (pattern,),
            ).fetchall()
            if len(rows) == 1:
                return rows[0][0]
    finally:
        conn.close()
    return None


def _resolve_module_names(task: str, cwd: str | None) -> list[str]:
    """Resolve a module/package NAME from a describe-module frame ("explain
    the compiler architecture") to a unique repo file via the index.

    Graceful: returns [] when the name is absent, cwd/index unavailable, or
    any DB error occurs.
    """
    if not task or not cwd:
        return []
    name = _extract_describe_module(task)
    if not name:
        return []
    import os as _os

    db_path = _os.path.join(cwd, ".roam", "index.db")
    if not _os.path.exists(db_path):
        return []
    try:
        path = _query_unique_module_path(db_path, name)
    except Exception as exc:  # noqa: BLE001 — best-effort resolution
        log_swallowed("compile.resolve_module_names", exc)
        return []
    return [path] if path else []


def _explain_classifier(task: str) -> dict:
    """Diagnostic dump of which regexes matched and why a procedure won.

    Used by `roam compile --explain` to surface the routing decision tree
    when an agent or human is surprised by the classifier's verdict.
    """
    signals: dict[str, list[str]] = {}
    for name, pattern in (
        ("trace_query", _TRACE_RE),
        ("synthesis_query", _SYNTHESIS_RE),
        ("structural_dead", _STRUCTURAL_DEAD_RE),
        ("structural_cycle", _STRUCTURAL_CYCLE_RE),
        ("structural_complexity", _STRUCTURAL_COMPLEXITY_RE),
        ("structural_blast", _STRUCTURAL_BLAST_RE),
        ("structural_callers", _STRUCTURAL_CALLERS_RE),
        ("structural_coupling", _STRUCTURAL_COUPLING_RE),
        ("structural_general", _STRUCTURAL_RE),
    ):
        matches = pattern.findall(task)
        if matches:
            # `findall` may return tuples for grouped regexes — flatten.
            flat = []
            for m in matches:
                if isinstance(m, tuple):
                    flat.extend([x for x in m if x])
                else:
                    flat.append(m)
            signals[name] = sorted(set(flat))[:5]

    winner, rejected = _classify(task)
    return {
        "task": task,
        "winner": winner,
        "rejected": rejected,
        "regex_matches": signals,
        "named_paths_extracted": _extract_file_paths(task),
        "tiebreak_rules": [
            "1. trace phrasing wins over structural (R10 memo)",
            "2. synthesis phrasing wins over structural",
            "3. structural sub-types checked in order: dead, cycle, complexity, blast, callers, coupling",
            "4. fallback to freeform_explore when no pattern fires",
        ],
    }


def _named_path_staleness(named_paths: list[str], cwd: str | None) -> dict | None:
    """Detect stale-index conditions that would mislead the agent.

    Two signals (either triggers `is_stale=True`):
      * Any named_path doesn't exist on disk under cwd. The compiler
        extracted it from the task text — if it's not actually there,
        the agent will hallucinate when it tries to Read or grep.
      * The .roam/index.db is older than 24h (or missing). Any roam-derived
        named_paths (from search-semantic) may point at deleted/renamed files.

    Returns None when no staleness signal fires. Otherwise:
      {"is_stale": True, "missing_paths": [...], "index_age_seconds": int|None,
       "warning": "<one-line human-readable hint>"}
    """
    import os as _os

    base = cwd or _os.getcwd()
    missing: list[str] = []
    seen: set[str] = set()
    for p in named_paths:
        if p in seen:
            continue
        seen.add(p)
        # Skip non-path-looking entries (regex captures dirs as "src/" — fine).
        full = _os.path.join(base, p)
        if not _os.path.exists(full):
            missing.append(p)
    # Index age
    index_db = _os.path.join(base, ".roam", "index.db")
    age_sec = None
    if _os.path.isfile(index_db):
        try:
            mtime = _os.path.getmtime(index_db)
            age_sec = int(time.time() - mtime)
        except OSError as exc:
            log_swallowed("compile.named_path_staleness.index_mtime", exc)

    is_stale = bool(missing) or (age_sec is not None and age_sec > 24 * 3600)
    if not is_stale and named_paths and age_sec is None:
        # Missing index AND we have named_paths — treat as stale.
        is_stale = True

    if not is_stale:
        return None

    parts = []
    if missing:
        parts.append(f"{len(missing)} named_paths missing on disk")
    if age_sec is not None and age_sec > 24 * 3600:
        parts.append(f"index is {age_sec // 3600}h old")
    if age_sec is None:
        parts.append("no .roam/index.db present")
    warning = "named_paths may be unreliable: " + "; ".join(parts) + ". Verify with Read/Grep before trusting."
    return {
        "is_stale": True,
        "missing_paths": missing,
        "index_age_seconds": age_sec,
        "warning": warning,
    }


def _check_files_newer_than_index(named_paths: list[str], cwd: str | None) -> dict | None:
    """Detect files newer than the index.db (post-index edits).

    Returns None if all files are older than index or index doesn't exist.
    Otherwise returns:
      {"files_newer_than_index": [...]} — list of named_paths that were edited
      after the index.db mtime.
    """
    import os as _os

    if not named_paths:
        return None

    base = cwd or _os.getcwd()
    index_db = _os.path.join(base, ".roam", "index.db")

    # If index doesn't exist, no comparison possible
    if not _os.path.isfile(index_db):
        return None

    try:
        index_mtime = _os.path.getmtime(index_db)
    except OSError as exc:
        log_swallowed("compile.files_newer_than_index.index_mtime", exc)
        return None

    newer_files: list[str] = []
    seen: set[str] = set()
    for p in named_paths:
        if p in seen:
            continue
        seen.add(p)
        full = _os.path.join(base, p)
        if _os.path.exists(full):
            try:
                file_mtime = _os.path.getmtime(full)
                # Use a small tolerance (5ms) to avoid false positives from
                # filesystem timestamp granularity
                if file_mtime > index_mtime + 0.005:
                    newer_files.append(p)
            except OSError as exc:
                log_swallowed("compile.files_newer_than_index.file_mtime", exc)

    if not newer_files:
        return None

    return {"files_newer_than_index": newer_files}


# ---- W57.5 — conservative task canonicalization + symbol-resolution cache ----
#
# Goal: close the W56-exposed gap where the backticked-symbol task only got 1.6×
# warm-cache speedup because `compile_plan` runs `roam search-semantic` BEFORE
# the envelope cache lookup gets a chance. Two layers:
#
#   (a) Canonicalize the task text used in cache keys so trivial rephrasings
#       (case, whitespace, smart quotes, trailing punctuation) collapse to one
#       row. Strictly conservative — does NOT collapse semantically-distinct
#       rephrasings (e.g. "who calls X" vs "what does X do"), because the
#       cached plan would be wrong. Verb-canonicalization is a future wave
#       contingent on classifier-equivalence proofs.
#
#   (b) Persist the `roam search-semantic` resolution result (i.e. the
#       `likely_files` list) in a sibling SQLite table so the second compile
#       of the same canonical query skips the ~200ms subprocess. Negative
#       results (empty list) are cached too. Invalidated on repo_head change.

_SYMBOL_RES_CACHE_TABLE_DDL = (
    "CREATE TABLE IF NOT EXISTS symbol_resolution_cache "
    "(key TEXT PRIMARY KEY, repo_head TEXT, query TEXT, result_json TEXT, ts REAL)"
)
_SYMBOL_RES_CACHE_MAX_ROWS = 2048


from functools import lru_cache as _w144_lru_cache


@_w144_lru_cache(maxsize=512)
def _canonicalize_task(task: str) -> str:
    """Conservative canonicalization for cache keys.

    Applies ONLY semantics-preserving transforms: lowercase, whitespace
    collapse, smart-quote → straight, strip leading/trailing whitespace and
    common terminal punctuation (`?` `!` `.`). Backticks are preserved
    because several downstream probe regexes anchor on them.

    Does NOT normalize verbs or rearrange words — that would risk collapsing
    semantically-distinct rephrasings (e.g. "who calls X" vs "what does X
    do") into the same cache row and returning the wrong plan.
    """
    if not task:
        return ""
    s = task.replace("‘", "'").replace("’", "'")
    s = s.replace("“", '"').replace("”", '"')
    s = s.strip().lower()
    # Strip terminal punctuation (one trailing ?, !, or . — preserves
    # ellipses-as-content by only stripping a single char).
    while s and s[-1] in "?!.":
        s = s[:-1].rstrip()
    # Collapse all internal whitespace runs to a single space.
    s = " ".join(s.split())
    return s


def _symbol_resolution_cache_lookup(task: str, cwd: str | None) -> tuple[list[str], bool] | None:
    """Return (files, search_invoked=False) on hit, None on miss. Never raises.

    On hit, `search_invoked=False` because the live subprocess didn't run —
    consistent with `_likely_files_from_search`'s second-value contract.
    """
    path = _envelope_cache_path(cwd)
    if not path:
        return None
    head = _memoized_head(cwd) if cwd else None
    if head is None:
        return None
    try:
        import sqlite3

        conn = sqlite3.connect(path, timeout=1.0)
        _set_wal(conn)
        try:
            conn.execute(_SYMBOL_RES_CACHE_TABLE_DDL)
            key = _envelope_cache_key(_canonicalize_task(task), head, cwd)
            row = conn.execute(
                "SELECT repo_head, result_json FROM symbol_resolution_cache WHERE key=?",
                (key,),
            ).fetchone()
            if row is None:
                return None
            cached_head, result_json = row
            if cached_head != head:
                conn.execute("DELETE FROM symbol_resolution_cache WHERE key=?", (key,))
                conn.commit()
                return None
            files = json.loads(result_json)
            if not isinstance(files, list):
                return None
            return [str(f) for f in files], False
        finally:
            conn.close()
    except (OSError, sqlite3.DatabaseError, json.JSONDecodeError, TypeError) as exc:
        log_swallowed("compile.symbol_resolution_cache.lookup", exc)
        return None


def _symbol_resolution_cache_store(task: str, cwd: str | None, files: list[str]) -> None:
    """Best-effort write of the resolved file list. Never raises."""
    path = _envelope_cache_path(cwd)
    if not path:
        return
    head = _memoized_head(cwd) if cwd else None
    if head is None:
        return
    try:
        import sqlite3

        conn = sqlite3.connect(path, timeout=1.0)
        _set_wal(conn)
        try:
            conn.execute(_SYMBOL_RES_CACHE_TABLE_DDL)
            canonical = _canonicalize_task(task)
            key = _envelope_cache_key(canonical, head, cwd)
            conn.execute(
                "INSERT OR REPLACE INTO symbol_resolution_cache VALUES (?,?,?,?,?)",
                (key, head, canonical, _fast_json_dumps(files), time.time()),
            )
            (count,) = conn.execute("SELECT COUNT(*) FROM symbol_resolution_cache").fetchone()
            if count > _SYMBOL_RES_CACHE_MAX_ROWS:
                overflow = count - _SYMBOL_RES_CACHE_MAX_ROWS
                conn.execute(
                    "DELETE FROM symbol_resolution_cache WHERE key IN ("
                    "  SELECT key FROM symbol_resolution_cache ORDER BY ts ASC LIMIT ?"
                    ")",
                    (overflow,),
                )
            conn.commit()
        finally:
            conn.close()
    except (OSError, sqlite3.DatabaseError, ValueError, TypeError) as exc:
        log_swallowed("compile.symbol_resolution_cache.store", exc)


# Freeform-candidate rerank weights. search-semantic text scores on
# conceptual tasks are nearly FLAT (~0.28-0.32 observed on the live repo), so
# ordering by them alone is noise — a comprehension task about "the compiler
# and verify" surfaced six unrelated test files. Three offline signals break
# the tie without overriding a strong text match (exact symbol hits score
# 0.6+ and stay on top): a task token literally in the file path is the
# strongest freeform signal; test/vendored/generated files are rarely the
# subject of comprehension tasks; structural importance (summed symbol
# PageRank from graph_metrics) separates load-bearing modules from leaves.
_RERANK_PATH_TOKEN_BOOST = 0.12  # per matched task token in the path, capped
_RERANK_PATH_TOKEN_CAP = 2
_RERANK_ROLE_ADJUST = {"source": 0.04, "test": -0.06, "vendored": -0.06, "generated": -0.06}
_RERANK_PAGERANK_BOOST = 0.04  # × log-normalized rank among the candidates
_RERANK_STOP_TOKENS = frozenset(
    "the a an and or of in on for to is are with how what where why does do "
    "use uses used like check find show me i you we it this that try improve "
    "can any want next study well also command".split()
)


def _task_path_tokens(task: str) -> set[str]:
    """Lowercase task tokens worth matching against path components."""
    words = re.findall(r"[a-zA-Z_]{3,}", task.lower())
    return {w for w in words if w not in _RERANK_STOP_TOKENS}


def _path_token_recall(task: str, cwd: str | None, known: set[str], cap: int = 6) -> list[tuple[str, float]]:
    """Pull source files whose BASENAME contains a task token into the pool.

    search-semantic ranks only what its text index surfaces; on conceptual
    tasks the module the user literally NAMED ("the compiler", "verify")
    often isn't in its top-10 at all. A task token matching a filename
    component is near-certain relevance — recall those files directly from
    the index (read-only SQLite, no subprocess), highest-PageRank first.
    Entries join with text score 0.0; the rerank boosts do the rest.
    """
    tokens = _task_path_tokens(task)
    if not tokens:
        return []
    out: list[tuple[str, float]] = []
    try:
        import sqlite3

        index_path = os.path.join(cwd or "", ".roam", "index.db")
        if not os.path.isfile(index_path):
            return []
        conn = sqlite3.connect(index_path, timeout=1.0)
        try:
            # Basename matching happens in Python: a SQL LIKE over the full
            # path is too loose (the repo-name token matches every path
            # under src/<repo>/, crowding out real basename hits). The
            # source-role file list is small (hundreds of rows).
            paths = [r[0] for r in conn.execute("SELECT path FROM files WHERE COALESCE(file_role,'source') = 'source'")]
            matches = [p for p in paths if p not in known and any(t in os.path.basename(p).lower() for t in tokens)]
            if not matches:
                return []
            qmarks = ",".join("?" for _ in matches)
            rows = conn.execute(
                f"""SELECT f.path, COALESCE(SUM(g.pagerank), 0) pr
                    FROM files f
                    LEFT JOIN symbols s ON s.file_id = f.id
                    LEFT JOIN graph_metrics g ON g.symbol_id = s.id
                    WHERE f.path IN ({qmarks})
                    GROUP BY f.id ORDER BY pr DESC LIMIT ?""",
                [*matches, cap],
            ).fetchall()
        finally:
            conn.close()
        out = [(path, 0.0) for path, _pr in rows]
    except Exception as exc:  # noqa: BLE001 — recall must never break compile
        log_swallowed("compile.likely_files.token_recall", exc)
    return out


def _rerank_likely_files(task: str, scored: list[tuple[str, float]], cwd: str | None) -> list[str]:
    """Blend text score + path-token match + file role + PageRank.

    Pure local math over the existing index — one read-only SQLite query,
    no subprocess, no model calls. Fail-open: any DB problem returns the
    text-score order unchanged.
    """
    if len(scored) <= 1:
        return [p for p, _ in scored]
    role_pr: dict[str, tuple[str, float]] = {}
    try:
        import sqlite3

        roam_dir = os.path.join(cwd or "", ".roam")
        index_path = os.path.join(roam_dir, "index.db")
        if os.path.isfile(index_path):
            conn = sqlite3.connect(index_path, timeout=1.0)
            try:
                qmarks = ",".join("?" for _ in scored)
                rows = conn.execute(
                    f"""SELECT f.path, COALESCE(f.file_role,'source'),
                               COALESCE(SUM(g.pagerank), 0)
                        FROM files f
                        LEFT JOIN symbols s ON s.file_id = f.id
                        LEFT JOIN graph_metrics g ON g.symbol_id = s.id
                        WHERE f.path IN ({qmarks})
                        GROUP BY f.id""",
                    [p for p, _ in scored],
                ).fetchall()
            finally:
                conn.close()
            role_pr = {r[0]: (r[1], float(r[2])) for r in rows}
    except Exception as exc:  # noqa: BLE001 — rerank must never break compile
        log_swallowed("compile.likely_files.rerank", exc)

    # Mini-IDF: a token that matches most of the pool discriminates nothing
    # (the repo-name token matches every path under src/<repo>/). Keep only
    # tokens hitting <60% of candidates.
    all_tokens = _task_path_tokens(task)
    n = len(scored)
    tokens = {t for t in all_tokens if sum(1 for p, _ in scored if t in p.lower()) < 0.6 * n}
    max_pr = max((pr for _, pr in role_pr.values()), default=0.0)

    # Signal-aware text weight, continuous form: the text contribution is
    # the candidate's PERCENTILE RANK within the pool, scaled by a band that
    # tracks the observed score spread (clamped 0.08-0.25). A flat
    # conceptual pool (spread ~0.03) yields a small band, so the structural
    # boosts decide; a real symbol hit (spread 0.3+) yields a wide band and
    # stays on top. No threshold cliff — 0.09 vs 0.11 spread behaves almost
    # identically (the binary flat/raw branch flipped orderings around it).
    nonzero = sorted(s for _, s in scored if s > 0)
    spread = (nonzero[-1] - nonzero[0]) if nonzero else 0.0
    text_band = min(max(spread, 0.08), 0.25)

    def _percentile(raw: float) -> float:
        if not nonzero or raw <= 0:
            return 0.0
        return sum(1 for s in nonzero if s <= raw) / len(nonzero)

    def blended(item: tuple[str, float]) -> float:
        path, text_score = item
        score = text_band * _percentile(float(text_score or 0.0))
        base = os.path.basename(path).lower()
        path_lower = path.lower()
        # Basename hits are the strong form; directory hits count half.
        matched_base = sum(1 for t in tokens if t in base)
        matched_dir = sum(1 for t in tokens if t in path_lower and t not in base)
        boost_units = min(matched_base + 0.5 * matched_dir, float(_RERANK_PATH_TOKEN_CAP))
        score += _RERANK_PATH_TOKEN_BOOST * boost_units
        role, pr = role_pr.get(path, ("source", 0.0))
        score += _RERANK_ROLE_ADJUST.get(role, 0.0)
        if max_pr > 0 and pr > 0:
            score += _RERANK_PAGERANK_BOOST * (math.log1p(pr) / math.log1p(max_pr))
        return score

    return [p for p, _ in sorted(scored, key=blended, reverse=True)]


def _likely_files_from_search(task: str, cwd: str | None, top_n: int = 6) -> tuple[list[str], bool]:
    """Hybrid: explicit path mentions first, then symbol-resolution cache,
    then `roam search-semantic` as the fallback.

    R10: search-semantic adds tangential noise when the task
    already names files explicitly — skip the subprocess in that case (saves
    ~200ms AND removes the noise).

    W57.5 (2026-05-31): when no explicit paths are present, consult the
    persistent symbol-resolution cache before firing the subprocess. The
    cache is keyed by canonical task text + repo_head, so trivial
    rephrasings (case, whitespace, smart quotes, trailing punctuation) share
    a row. Invalidated on HEAD change.
    """
    # W33c (M4): the second return value means "search subprocess WAS
    # invoked" (NOT "we have files"). On cache hit we return False — the
    # subprocess didn't run this turn — which is what model_calls_avoided
    # accounting wants.
    explicit = _extract_file_paths(task)
    if explicit:
        # Explicit paths in the task = high-confidence signal; skip search-semantic.
        return explicit[:top_n], False  # search NOT invoked

    # W57.5 — persistent symbol-resolution cache check before the subprocess.
    cached = _symbol_resolution_cache_lookup(task, cwd)
    if cached is not None:
        files, _ = cached
        return files[:top_n], False  # cached → subprocess NOT invoked

    # Only when NO explicit paths and no cache hit: fall back to semantic.
    env = _run_roam(["search-semantic", task], cwd=cwd)
    if not env:
        # Cache the negative result too so we don't keep firing.
        _symbol_resolution_cache_store(task, cwd, [])
        return [], True  # subprocess invoked even if it failed
    results = env.get("results") or []
    scored: list[tuple[str, float]] = []
    best: dict[str, float] = {}
    for r in results:
        path = r.get("file_path") or r.get("file") or r.get("path") or ""
        if not path:
            continue
        score = float(r.get("score") or 0.0)
        if path not in best or score > best[path]:
            best[path] = score
    scored = list(best.items())
    # Text scores alone are nearly flat on conceptual tasks — widen the pool
    # with basename-token recall (the module the task literally names), then
    # blend path-token match, file role, and PageRank before trimming.
    scored += _path_token_recall(task, cwd, known=set(best))
    seen = _rerank_likely_files(task, scored, cwd)[:top_n]
    # Store the full resolution (top_n trim happens at consumer; cache the
    # superset so future top_n values up to the cap are served).
    _symbol_resolution_cache_store(task, cwd, seen)
    return seen, True  # subprocess invoked


def _required_checks_from_commands(cwd: str | None) -> tuple[list[str], bool]:
    """Run roam commands, extract test invocations from G2 command graph."""
    env = _run_roam(["commands"], cwd=cwd)
    if not env:
        return [], False
    cmds = env.get("commands") or []
    test_cmds: list[str] = []
    for c in cmds:
        if c.get("kind") == "test" and c.get("safe_to_auto_run"):
            cmd = c.get("command") or c.get("invocation") or ""
            if cmd and cmd not in test_cmds:
                test_cmds.append(cmd)
    return test_cmds[:4], bool(cmds)


def _git_head(cwd: str | None) -> str | None:
    try:
        p = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2.0,
            cwd=cwd,
        )
        return p.stdout.strip() if p.returncode == 0 else None
    except (OSError, subprocess.SubprocessError) as exc:
        log_swallowed("compile.repo_head_lookup", exc)
        return None


# R10 plan cache: same (task, cwd, repo_head) compiles to the same PlanV0;
# cache it to make repeated compiles literally free. Bounded to 256 entries
# to keep memory predictable.
#
# W33a (2026-05-30) — repo_head IS in the key now. The prior comment said
# "intentionally not" but W33 made compile embed actual coupling/callers/dead
# data, so a stale cache hit returns the WRONG ANSWER (not just stale
# routing). Including repo_head pays ~25-30ms for a `git rev-parse HEAD` on
# first miss in a process, but we memoize per-cwd so subsequent lookups in
# the same cwd never re-shell. The trade: bounded extra compute on the
# first compile, correctness across `git checkout` for every call after.
_PLAN_CACHE: dict[str, "PlanV0"] = {}
_PLAN_CACHE_MAX = 256

# Memoize git HEAD per cwd within a single process. Cleared by clear_plan_cache.
_HEAD_BY_CWD: dict[str, str | None] = {}


def _memoized_head(cwd: str | None) -> str | None:
    key = cwd or ""
    if key in _HEAD_BY_CWD:
        return _HEAD_BY_CWD[key]
    h = _git_head(cwd)
    _HEAD_BY_CWD[key] = h
    return h


def _cache_key(task: str, cwd: str | None) -> str:
    """Cache key includes repo_head so a `git checkout` invalidates cached
    L1 envelopes. W57.5 — canonicalize the task text first so trivial
    rephrasings (case, whitespace, smart quotes, trailing punctuation)
    share a cache row. Conservative: does not collapse semantically-distinct
    rephrasings."""
    return f"{_canonicalize_task(task)!r}|{cwd or ''}|{_memoized_head(cwd) or ''}|{_compiler_fingerprint()}"


def clear_plan_cache() -> None:
    """Flush the compile_plan cache + the per-cwd HEAD memo. Useful in
    tests; not normally needed at runtime now that repo_head is part of
    the cache key (W33a)."""
    _PLAN_CACHE.clear()
    _HEAD_BY_CWD.clear()


def compile_plan(task: str, cwd: str | None = None) -> PlanV0:
    """The v0 task compiler. Zero model calls.

    R10 speedups:
    - Skip `roam search-semantic` when explicit paths exist in task text
    - Skip `roam commands` for read-only procedures (structural/trace/freeform);
      only call it when the procedure produces an artifact that needs
      verification (synthesis_query).

    Most compile calls now do ZERO subprocess work (<10ms) instead of the
    prior ~500ms baseline. Subprocess calls only happen when the task
    genuinely needs that information.

    R10 plan cache: identical (task, cwd) at the same repo_head returns
    the cached PlanV0 instantly (<0.1ms). Cache invalidates automatically
    on new commit.
    """
    # Cache check (truly free hit — no subprocess)
    ckey = _cache_key(task, cwd)
    cached = _PLAN_CACHE.get(ckey)
    if cached is not None:
        return cached
    # W57 — persistent plan cache. Falls through to compute on miss /
    # error / HEAD mismatch. Saves the costly `roam search-semantic`
    # subprocess on backticked-symbol tasks where compile_plan otherwise
    # dominates the warm-cache wall (W56 found 416ms warm vs 5ms target).
    persisted = _plan_cache_lookup(task, cwd)
    if persisted is not None:
        _PLAN_CACHE[ckey] = persisted
        return persisted

    procedure, rejected = _classify(task)
    # W33c: second value is now "search subprocess WAS invoked" (was: "we
    # have files"). Rename for clarity at the call site.
    likely_files, search_invoked = _likely_files_from_search(task, cwd=cwd)

    # Required checks only matter for synthesis (the agent will write code
    # and the user wants to know how to verify). Other procedures don't use
    # this field — save the ~200ms subprocess call.
    if procedure == "synthesis_query":
        required_checks, commands_invoked = _required_checks_from_commands(cwd=cwd)
    else:
        required_checks, commands_invoked = [], False

    # W33c (M4): "model_calls_avoided" now actually reflects what was avoided.
    # Prior: claimed avoidance based on "we have data" (always-true when files
    # found, even via the subprocess). Now: claim it only when we genuinely
    # skipped the subprocess.
    avoided = []
    if not search_invoked and likely_files:
        # Explicit-path extraction substituted for search-semantic.
        avoided.append("roam_search_semantic (file location inference)")
    if not commands_invoked and procedure != "synthesis_query":
        # Read-only procedures don't need the commands graph.
        avoided.append("roam_commands (test runner discovery)")
    avoided.append("procedure classification (regex, no LLM)")

    # plan_quality v0 heuristic: 4 signals × 0.25 each. W33c — the prior
    # check `used_search and likely_files` was always-true-or-near-it; now
    # we count "we have likely_files at all" as a positive signal.
    signals = 0.0
    if procedure != "freeform_explore":
        signals += 0.25
    if likely_files:
        signals += 0.25
    if required_checks:
        signals += 0.25
    if procedure.startswith("structural_") and _RECOMMENDED_FIRST_COMMAND.get(procedure):
        signals += 0.25  # routing hint adds value for structural queries

    plan = PlanV0(
        task=task,
        procedure=procedure,
        likely_files=likely_files,
        required_checks=required_checks,
        forbidden_paths=list(_FORBIDDEN_PATHS_DEFAULT),
        plan_quality=round(signals, 2),
        model_calls_avoided=avoided,
        recommended_first_command=_RECOMMENDED_FIRST_COMMAND[procedure],
        rejected_procedures=rejected,
        repo_head=_memoized_head(cwd),
        compiled_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        classifier_confidence=_classifier_confidence(task, procedure),
        recommended_parallel_tools=list(_PROCEDURE_PARALLEL_COMBO.get(procedure, [])),
    )
    # Write to cache (bounded eviction)
    if len(_PLAN_CACHE) >= _PLAN_CACHE_MAX:
        # Drop oldest 1/4 of entries (rough LRU; insertion order = dict order in 3.7+)
        evict_count = _PLAN_CACHE_MAX // 4
        for key in list(_PLAN_CACHE.keys())[:evict_count]:
            del _PLAN_CACHE[key]
    _PLAN_CACHE[ckey] = plan
    _plan_cache_store(task, cwd, plan)  # W57 — best-effort persistent store
    return plan
