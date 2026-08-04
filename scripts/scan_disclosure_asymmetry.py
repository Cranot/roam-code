#!/usr/bin/env python3
"""W1331 — scan ``src/roam/commands`` for output-branch disclosure asymmetry.

THE DEFECT SHAPE THIS FINDS
---------------------------
A command computes a *disclosure signal* — ``warnings_out``,
``empty_corpus_state()``, ``truncation_reason``, … — which says "this
result is degraded / could not be computed". The disclosure then gets
written into ONE output branch (nearly always the ``if json_mode:``
branch, because that is the branch under test) and is never mirrored into
the sibling branches. The JSON envelope honestly reports "this signal
could not be computed" while the TEXT a human reads and the SARIF a CI
gate consumes both report clean.

Three of the four false-clean defects fixed in W1320 had exactly this
shape, e.g.:

* ``py-types --ci --min-coverage 90`` at 0% coverage: the gate sat after
  the json/sarif early returns, so TEXT exited 5 while the two modes CI
  actually consumes exited 0 on the identical number.
* ``delete-check``: ``matches = []`` on engine failure, threaded into
  ``warnings_out`` and emitted in the JSON branch — the ``else:`` branch
  printed only the verdict.

HOW THE SCAN WORKS
------------------
For every ``cmd_*.py`` it parses the click command function and partitions
its statements into *output-mode regions* by simulating flow through the
mode predicates (``json_mode``, ``sarif_mode``, ``ctx.obj.get("json")``):

* an ``if json_mode: ... else: ...`` splits the active mode set;
* an early ``return`` / ``ctx.exit()`` inside a mode branch REMOVES that
  mode from the active set for every following statement — this is what
  makes the ``py-types`` shape (gate written after the json early return)
  visible as an asymmetry rather than as shared code;
* statements before any dispatch are shared, and therefore observed by
  every mode.

Calls are then followed one module deep, transitively, so a disclosure
passed only to ``_emit_x_json(...)`` is attributed to json alone while a
disclosure computed before the dispatch is attributed to all modes.

A mode counts as *supported* only if some statement attributed to it
actually emits (``click.echo`` / ``to_json`` / ``print``) — so a bare
``if json_mode:`` guard around bookkeeping does not invent a branch.

For SARIF, a text warning written beside the document does not count as
disclosure in the artifact. The signal must be passed into a SARIF builder
(``*_to_sarif`` / ``to_sarif``), normally as a runtime notification.

A violation is: mode A observes disclosure token T and supported mode B
does not.

THE SCANNER FAILS CLOSED (W1455)
--------------------------------
Every file gets one of THREE outcomes — ``clean``, ``violation``, or
``unanalyzable`` — never the two-valued ``[] means fine`` this scanner
itself shipped with. Before W1455 a single ``def broken(:`` anywhere in
``src/roam/commands`` made that module vanish from both the scan and the
coverage enumeration while the gate printed success: an unparseable file was
byte-identical to a clean repository. That is the very defect class this
scanner exists to find.

So the report publishes its DENOMINATOR — ``files_parsed``,
``files_unanalyzable``, ``files_skipped`` — a cannot-analyse exits non-zero
just like a violation, and every invocation runs a POSITIVE CONTROL over
``tests/fixtures/scanner_positive_controls/disclosure``: a planted
asymmetry that must be found, beside its symmetric twin that must not be.
Without that pair, "0 findings" and "the detector stopped working" are the
same output.

USAGE
-----
    python scripts/scan_disclosure_asymmetry.py            # JSON report
    python scripts/scan_disclosure_asymmetry.py --text     # human report
    python scripts/scan_disclosure_asymmetry.py --baseline # rewrite the ratchet IN PLACE

Exit codes: 0 clean, 1 violations, 2 unanalyzable files, 3 the scanner
cannot be trusted (positive control failed, or nothing was parsed at all).

``--baseline`` regenerates ``tests/data/disclosure_asymmetry_baseline.json``,
the SHRINKING baseline consumed by
``tests/test_w1331_disclosure_branch_symmetry.py``.  The tree is not clean,
so the ratchet is a high-water mark that may only fall: the test fails on a
NEW violation *and* on a STALE entry.  Every entry names a reason code from
``REASON_CODES``; a violation shape with no mapping emits ``"TODO"``, which
that test rejects — an allowlist whose entries do not say why is a shrug,
not a policy.

THE REGENERATE COMMAND WRITES THE FILE; DO NOT REDIRECT IT (W1460)
------------------------------------------------------------------
``--baseline`` used to print the ratchet to STDOUT and the documented
remediation was::

    python scripts/scan_disclosure_asymmetry.py --baseline > tests/data/disclosure_asymmetry_baseline.json  # W1460-HISTORICAL: the trap. Never run this.

That instruction destroyed the artifact it claimed to regenerate, on every
single run. The shell opens and TRUNCATES the redirect target before this
process starts, so by the time ``_carried_mark`` reads the recorded
high-water mark and its rationale, the file it is reading is zero bytes.
Measured at 4a358387: exit 2, ``REFUSING to emit a baseline: the existing
ratchet file could not be read (JSONDecodeError...)``, and a 0-byte ratchet
left on disk — mark, rationale, all four entries and the reason-code table
gone, and no way to get them back but ``git checkout``. The command could
never succeed: deleting the "corrupt" file and re-running fails identically,
because the redirect recreates it empty every time.

The fix is that ``--baseline`` writes ``baseline_path()`` itself and puts
NOTHING on stdout, so there is no redirect left to type. Reading earlier
cannot fix this and never could — the truncation happens in the shell, before
``main`` is entered — which is why the redirect is removed from the contract
rather than documented around. The residual case, a crash or a kill partway
through the write, is closed by writing to a sibling temp file and
``os.replace``-ing it into place: the ratchet is either the old file or the
new one, never half of either.

The pre-W1460 refusal was itself a fix, and an incomplete one: it converted
a SILENT reset of the high-water mark into a LOUD refusal, which stopped the
mark being laundered but left the file destroyed. A guard that turns silent
data loss into loud data loss has moved the defect, not removed it.

The baseline is a list of ASYMMETRIES, never a list of unreadable files:
``files_unanalyzable`` has no baseline and never will, because "I could not
check this" is not a grandfathered violation, it is a broken gate.

A MARK THAT CANNOT BE READ IS NOT A MARK OF ZERO (W1471)
---------------------------------------------------------
W1460 closed the file-level route to a laundered ceiling: an ABSENT ratchet
is a first generation, anything PRESENT-but-unreadable is a refusal. It left
the FIELD-level route open. ``_carried_mark`` tested the recorded mark with a
bare ``isinstance(mark, int)`` and fell back to the current count on every
way of failing it, so a ratchet that parsed perfectly well but whose
``_high_water_mark`` was missing — or spelled ``"68"``, ``68.0`` or ``null``
— silently reset the ceiling to today's count and exited 0. Measured at
8288593e against a planted repo: mark 99 in, mark 1 out, no diagnostic.

Worse than the zero-byte case it followed, on two counts. The zero-byte file
REFUSED; this one succeeded. And the *rationale* was carried forward
untouched, so the emitted file paired today's count with the prose written to
justify a much larger number — a ratchet that reads as reviewed. The reviewer
sees a plausible file; the ceiling is gone.

So the mark and its note are now validated, not merely sniffed, and refused
together: only a genuinely absent FILE is a first generation, and a present
one must carry a non-negative integer mark AND a non-empty reason. ``bool``
is rejected explicitly (``isinstance(True, int)`` is true, and a mark of
``True`` pins the ratchet at one entry). Substituting ``_UNEXPLAINED_MARK``
for a dropped note would have laundered it past
``test_a_raised_high_water_mark_says_why``, whose floor is a length check
that the placeholder clears.

EVERY EARLY EXIT IS PART OF THE CONTRACT (W1459)
------------------------------------------------
W1455 covered the OUTERMOST exit — the file this scanner could not read or
parse. It did not cover the exits INSIDE the analysis, and those had exactly
the shape W1455 was written to remove: ``_expand`` stopped its callee closure
at 4000 names, the token walk stopped at depth 12, and the return-value
fixpoint stopped after 3 rounds. Each of the three abandoned work it had
already committed to enumerating, and the file it was analysing still came
back ``CLEAN``. The denominator counted FILES; it did not count COMPLETENESS
OF THE ANALYSIS, so a partially-examined file was byte-identical to a fully
examined one — the same equivalence W1455 broke one level up.

The rule that follows, and that the code below is arranged around:

* **Class A — truncation.** An exit that abandons an enumeration already in
  progress: a cap, a budget, a round limit, a bail-out. Reaching one means
  this file was PARTIALLY examined. Partial is never clean, so it taints the
  file's status (``PARTIAL``), is counted per cap in ``cap_hits``, and exits
  non-zero. Every Class-A exit in this file routes through
  :class:`AnalysisBudget`; there are no others, and adding one without a
  ``budget.hit(...)`` beside it reintroduces the defect.
* **Class B — resolution imprecision.** The analysis models Python
  approximately and always has: calls are followed ONE MODULE deep, mode
  predicates are recognised by shape, and callees are resolved BY NAME. These
  are not early exits; they are the analysis's domain, uniform across every
  file. Tainting on them would mark all 275 files partial and destroy the
  signal, so they are REPORTED (``imprecision``) rather than gated — visible
  as a number, never as an absence.

Measured at HEAD before the caps were made loud: the callee closure peaks at
293 names against its 4000 limit, the fixpoint converges in 2 rounds of 3,
and the token walk reaches depth 10 against a limit of 12. None of the three
was firing, and removing all three changed neither the finding count (68) nor
the runtime (14.7s uncapped vs 15.1s capped) — so none of them was a time
budget. The depth cap had two levels of headroom, which is one helper-chain
refactor from truncating silently; it is gone, replaced by an explicit
worklist whose termination is guaranteed by the ``visited`` set instead of by
an arbitrary number.
"""

from __future__ import annotations

import argparse
import ast
import dataclasses
import json
import os
import pathlib
import sys

# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------

#: Disclosure tokens. A reference to one of these is a claim about the
#: TRUSTWORTHINESS of the result, not about the result itself — which is
#: why every output branch needs to see it. Matched as a bare name, an
#: attribute, a keyword argument, or a string literal (JSON envelopes
#: spell them as dict keys, text branches as variables).
DISCLOSURE_TOKENS: tuple[str, ...] = (
    "warnings_out",
    "empty_corpus_state",
    "truncation_reason",
    "scan_incomplete",
    # "degradation_state" -- PARKED, not abandoned. The rule and its
    # ``partial_success`` seeding machinery below are complete and correct;
    # what is NOT done is the repository-side work to satisfy it. Enabling it
    # flags 80 real asymmetries across commands whose text branches carry no
    # degradation marker, so turning it on before those are fixed makes the
    # gate red on main and teaches everyone to ignore it.
    #
    # It was briefly enabled by accident: the token was added here in an
    # uncommitted working tree ALONGSIDE the ~68 command fixes that satisfy
    # it, so it passed locally and failed CI at 80 -- the scanner read fixed
    # files locally and HEAD in CI. A rule and the work that satisfies it must
    # land together or the rule lands last.
    #
    # Every other reference to the token is guarded on membership in this
    # tuple, so parking it here disables the rule without deleting the work.
)

#: Tokens that are ENVELOPE KEY NAMES rather than computed signals: measured
#: on request, never gated by their spelling alone. A JSON branch carries them
#: by construction and a text branch never spells them, so a raw key-name
#: check degenerates into "the text branch is not JSON". The semantic
#: ``degradation_state`` rule above separately seeds when a command supplies
#: a ``partial_success`` value that can be true and accepts equivalent
#: text/SARIF prose.
#:
#: * ``partial_success`` — a key of the shared ``json_envelope`` schema. Its
#:   raw spelling stays reporting-only; a non-constant-false value is gated
#:   via ``degradation_state``.
#: * ``failed_checks`` — demoted here after BOTH live instances were run and
#:   found to disclose in every branch, just not under that name.
#:   ``adversarial`` with ``build_symbol_graph`` raising prints
#:   ``VERDICT: 2 high-severity of 131 challenges -- 3 check(s) errored:
#:   cross_cluster, layer_violations, new_cycles`` in TEXT — the identical
#:   list the envelope carries as ``summary.failed_checks``, because
#:   ``_compose_verdict`` appends it. ``doctor`` renders one ``[WARN]`` /
#:   ``[FAIL]`` line per entry plus a count line: four ``[WARN]`` rows in
#:   text against a four-name ``failed_checks`` array. Gating the KEY NAME
#:   there measured spelling, not disclosure.
SCHEMA_TOKENS: tuple[str, ...] = ("partial_success", "failed_checks")

# A non-zero text/SARIF exit is symmetric with a structured JSON failure
# envelope.  These tokens are collected only to compensate the gate rule;
# they are not independently reported as spelling differences.
_GATE_COMPENSATION_TOKENS: tuple[str, ...] = (
    "isError",
    "error_code",
)

# Cross-format semantic family. JSON usually spells the fact as
# ``partial_success``/``state`` while text and SARIF use prose.  Matching the
# family prevents the lint from degenerating into "text is not JSON".
_DEGRADATION_MARKERS: tuple[str, ...] = (
    "partial result",
    "truncat",
    "degrad",
    "incomplete",
    "scan_incomplete",
    "state=",
    "skipp",
    "unavailable",
    "warnings_out",
    "warning:",
    "failed_checks",
    "failed",
    "failure",
    "error_count",
    " errors",
    "error:",
    " error",
    "invalid",
    "omitted",
    "empty_corpus",
    "empty corpus",
    "not found",
    "no matches",
    "no symbols",
    "no files",
    "no code",
    "unresolved",
    "unknown",
    "missing",
    "not initialized",
    "not available",
    "cannot ",
    "no ",
    "not ",
    "blocked",
    "refused",
    "stale",
    "tamper",
    "absent",
    "disabled",
    "unsupported",
    "required",
)

#: Pseudo-token for the second rule: a NON-ZERO exit (a CI gate) that only
#: some output modes can reach. This is the ``py-types`` defect verbatim —
#: ``--ci --min-coverage 90`` at 0% coverage exited 5 in text and 0 in both
#: --json and --sarif, i.e. the two modes CI actually consumes passed on the
#: identical number, because the gate sat after their early returns.
#: Unlike the tokens above this is judged on plain REACHABILITY, not on
#: branch-specific placement: an exit in shared code is symmetric by
#: construction, and that is exactly what the fix looks like.
GATE_SIGNAL = "nonzero_exit"
_PARTIAL_TRUE_SEED = "_partial_success_true"

#: Names ending in one of these count as the token (``_w607cm_warnings_out``,
#: ``_combined_warnings_out``, … are all the same signal).
SUFFIX_TOKENS: frozenset[str] = frozenset({"warnings_out"})

MODES: tuple[str, ...] = ("json", "sarif", "text")

_EMIT_FUNCS: frozenset[str] = frozenset({"echo", "secho", "print", "to_json", "write", "echo_text_warnings"})


# --------------------------------------------------------------------------
# Mode predicates
# --------------------------------------------------------------------------


def _predicate_modes(node: ast.expr) -> set[str] | None:
    """Return the mode set a truthy ``node`` implies, or None.

    ``json_mode`` -> {"json"}; ``ctx.obj.get("sarif")`` -> {"sarif"};
    ``json_mode or sarif_mode`` -> {"json", "sarif"}.
    """
    if isinstance(node, ast.Name):
        if node.id in ("json_mode", "_json_mode"):
            return {"json"}
        if node.id in ("sarif", "sarif_mode", "_sarif_mode"):
            return {"sarif"}
        return None
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "get" and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and arg.value in ("json", "sarif"):
                return {str(arg.value)}
        return None
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
        parts = [_predicate_modes(v) for v in node.values]
        if all(p is not None for p in parts):
            merged: set[str] = set()
            for p in parts:
                merged |= p  # type: ignore[arg-type]
            return merged
    return None


class ModeTest:
    """A resolved ``if`` test: which modes reach the body vs the orelse."""

    __slots__ = ("modes", "negated", "else_narrows")

    def __init__(self, modes: set[str], negated: bool, else_narrows: bool) -> None:
        self.modes = modes
        self.negated = negated
        #: False for ``and``-compounded tests: the body is a SUBSET of the
        #: mode, but the else branch is not the complement, so nothing may
        #: be concluded about it.
        self.else_narrows = else_narrows


def _classify_test(test: ast.expr) -> ModeTest | None:
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        inner = _classify_test(test.operand)
        if inner is None:
            return None
        return ModeTest(inner.modes, not inner.negated, inner.else_narrows)
    direct = _predicate_modes(test)
    if direct is not None:
        return ModeTest(direct, negated=False, else_narrows=True)
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
        for value in test.values:
            sub = _classify_test(value)
            if sub is not None and not sub.negated:
                # ``json_mode and x`` -- the body is json-only, the else is
                # unconstrained.
                return ModeTest(sub.modes, negated=False, else_narrows=False)
    return None


def _terminates(body: list[ast.stmt]) -> bool:
    """True when control definitely leaves the function at the end of ``body``."""
    if not body:
        return False
    last = body[-1]
    if isinstance(last, (ast.Return, ast.Raise, ast.Continue, ast.Break)):
        return True
    if isinstance(last, ast.Expr) and isinstance(last.value, ast.Call):
        func = last.value.func
        if isinstance(func, ast.Attribute) and func.attr == "exit":
            return True
        if isinstance(func, ast.Name) and func.id == "exit":
            return True
    if isinstance(last, ast.If) and last.orelse:
        return _terminates(last.body) and _terminates(last.orelse)
    return False


# --------------------------------------------------------------------------
# Mode-region partitioning
# --------------------------------------------------------------------------

_BODY_FIELDS = ("body", "orelse", "finalbody")


def _contains_mode_test(node: ast.AST) -> bool:
    """True when ``node`` encloses an ``if json_mode:``-style dispatch."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.If) and _classify_test(sub.test) is not None:
            return True
    return False


class _Partition:
    """Attribute each statement of a command function to a set of modes."""

    def __init__(self) -> None:
        self.regions: dict[str, list[ast.AST]] = {m: [] for m in MODES}

    def record(self, node: ast.AST, active: set[str]) -> None:
        for mode in active:
            self.regions[mode].append(node)

    def walk(self, stmts: list[ast.stmt], active: set[str]) -> set[str]:
        """Walk ``stmts``; return the mode set still active after them."""
        for stmt in stmts:
            if isinstance(stmt, ast.If):
                mt = _classify_test(stmt.test)
                if mt is not None:
                    if mt.negated:
                        body_active = (active - mt.modes) if mt.else_narrows else active
                        else_active = active & mt.modes
                    else:
                        body_active = active & mt.modes
                        else_active = (active - mt.modes) if mt.else_narrows else active
                    self.record(stmt.test, active)
                    self.walk(stmt.body, body_active)
                    self.walk(stmt.orelse, else_active)
                    if mt.else_narrows:
                        if _terminates(stmt.body):
                            active = else_active
                        elif stmt.orelse and _terminates(stmt.orelse):
                            active = body_active
                    continue
                if not _contains_mode_test(stmt):
                    self.record(stmt, active)
                    continue
                self.record(stmt.test, active)
                self.walk(stmt.body, active)
                self.walk(stmt.orelse, active)
                continue
            if isinstance(stmt, (ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith, ast.Try)):
                if not _contains_mode_test(stmt):
                    # No nested dispatch: record the block WHOLE. The token
                    # and the emit routinely live in different parts of one
                    # block -- ``for marker in warnings_out: click.echo(...)``
                    # -- and splitting them scores the loop header as a
                    # disclosure that never prints anything.
                    self.record(stmt, active)
                    continue
                for child in ast.iter_child_nodes(stmt):
                    if isinstance(child, ast.expr):
                        self.record(child, active)
                for handler in getattr(stmt, "handlers", []):
                    self.walk(handler.body, active)
                for field in _BODY_FIELDS:
                    sub = getattr(stmt, field, None)
                    if isinstance(sub, list):
                        self.walk(sub, active)  # type: ignore[arg-type]
                continue
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # A closure defined here is reached wherever it is CALLED,
                # which the call-graph pass handles. Record it nowhere.
                continue
            self.record(stmt, active)
        return active


# --------------------------------------------------------------------------
# The completeness ledger (W1459)
# --------------------------------------------------------------------------

#: Class-A cap names. One per exit that can abandon an enumeration early.
CAP_CALLEE_CLOSURE = "callee_closure_limit"
CAP_RETURN_FIXPOINT = "return_fixpoint_rounds"
CAP_MODE_WALK = "mode_walk_steps"

#: Ceiling on the transitive callee closure of one module. ``seen`` is a
#: subset of the module's functions, so this can only bite on a module with
#: more functions than the limit — the largest in ``src/roam/commands`` has
#: 296. Kept rather than deleted because a generated module could exceed it,
#: and a bound that announces itself costs one comparison; what it must never
#: do again is stop the walk and let the file report clean.
_EXPAND_LIMIT = 4000


@dataclasses.dataclass
class AnalysisBudget:
    """What the analysis of ONE file gave up on, and what it approximated.

    ``caps_hit`` is Class A: an enumeration stopped early, so the file was
    only partially examined and its ``CLEAN`` is not a claim anyone may
    rely on. ``imprecision`` is Class B: a modelling limit that applies to
    every file equally and is therefore reported, not gated.

    The distinction is the whole point. Collapsing them would either make
    every file partial (Class B taints) or restore the silent truncation
    (Class A does not).
    """

    caps_hit: dict[str, int] = dataclasses.field(default_factory=dict)
    imprecision: dict[str, int] = dataclasses.field(default_factory=dict)

    def hit(self, cap: str) -> None:
        """Record that a Class-A cap stopped an enumeration."""
        self.caps_hit[cap] = self.caps_hit.get(cap, 0) + 1

    def approximate(self, kind: str, count: int = 1) -> None:
        """Record a Class-B modelling limit encountered while analysing."""
        if count:
            self.imprecision[kind] = self.imprecision.get(kind, 0) + count

    @property
    def complete(self) -> bool:
        """True when nothing was truncated — the precondition for ``CLEAN``."""
        return not self.caps_hit

    def describe(self) -> str:
        return ", ".join(f"{cap}x{n}" for cap, n in sorted(self.caps_hit.items()))


# --------------------------------------------------------------------------
# Module analysis
# --------------------------------------------------------------------------


def _matches_token(name: str, token: str) -> bool:
    if token == "degradation_state":
        lowered = name.lower()
        return any(marker in lowered for marker in _DEGRADATION_MARKERS)
    if name == token:
        return True
    return token in SUFFIX_TOKENS and name.endswith("_" + token)


def _carried_tokens(
    funcs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    vocabulary: tuple[str, ...],
    budget: AnalysisBudget,
) -> dict[str, set[str]]:
    """Tokens each in-module function can RETURN to its caller.

    ``_all_w_text = _merged_warnings()`` launders three ``*_warnings_out``
    buckets into a differently-named local. Without this the text branch
    that then prints ``_all_w_text`` scores as blind — a false positive on
    a command that discloses correctly.

    Only what a function RETURNS counts. Propagating every token a callee
    merely mentions would credit ``_run_check(...)`` — which appends to a
    bucket internally and returns a floored default — as if it handed the
    disclosure back, and that erases most real violations.

    The fixpoint runs TO CONVERGENCE. It used to run ``for _ in range(3)``
    on the reasoning that "return chains here are 1-2 deep" — true of the
    modules measured, and silently wrong for the fourth-deep chain nobody
    had written yet: the loop simply stopped with ``changed`` still true and
    handed back a closure it knew was unfinished. The bound below is not a
    bigger guess, it is the convergence proof: each round that changes
    anything advances the propagation frontier by at least one return-chain
    edge, and no simple chain is longer than the number of functions, so
    ``len(funcs) + 1`` rounds cannot be reached without convergence first.
    Reaching it anyway means the invariant is broken, and that is recorded
    rather than returned as a result.
    """
    returned: dict[str, set[str]] = {}
    for name, body in funcs.items():
        tokens: set[str] = set()
        for sub in ast.walk(body):
            if isinstance(sub, ast.Return) and sub.value is not None:
                tokens |= _node_tokens(sub.value, vocabulary)
        returned[name] = tokens
    for _ in range(max(len(funcs) + 1, 4)):
        changed = False
        for name, body in funcs.items():
            for sub in ast.walk(body):
                if not isinstance(sub, ast.Return) or sub.value is None:
                    continue
                for callee in _called_names(sub.value):
                    if callee in returned and not returned[callee] <= returned[name]:
                        returned[name] |= returned[callee]
                        changed = True
        if not changed:
            return returned
    budget.hit(CAP_RETURN_FIXPOINT)
    return returned


def _observed_tokens(
    node: ast.AST,
    vocabulary: tuple[str, ...],
    carried: dict[str, set[str]],
    aliases: dict[str, set[str]],
) -> set[str]:
    """Tokens ``node`` references directly, via a local alias, or via a call."""
    found = _node_tokens(node, vocabulary)
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id in aliases:
            found |= aliases[sub.id]
    for name in _called_names(node):
        found |= carried.get(name, set())
    return found


def _assigned_names(node: ast.AST) -> list[str]:
    targets: list[ast.expr] = []
    if isinstance(node, ast.Assign):
        targets = list(node.targets)
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        targets = [node.target]
    names: list[str] = []
    for target in targets:
        # Only a SINGLE-name binding aliases the whole value. A tuple
        # unpack -- ``a, b, c = _prepare(...)`` -- does not put the
        # disclosure in all three names, and pretending it does credited
        # cmd_auth_gaps' text branch for a call it never received the
        # bucket through.
        if isinstance(target, ast.Name):
            names.append(target.id)
    return names


def _node_tokens(node: ast.AST, vocabulary: tuple[str, ...]) -> set[str]:
    """Every disclosure token referenced anywhere under ``node``."""
    found: set[str] = set()
    for sub in ast.walk(node):
        if "degradation_state" in vocabulary:
            if isinstance(sub, ast.Dict):
                for key, value in zip(sub.keys, sub.values):
                    if (
                        isinstance(key, ast.Constant)
                        and key.value == "partial_success"
                        and not (isinstance(value, ast.Constant) and value.value is False)
                    ):
                        found.add("degradation_state")
                        found.add(_PARTIAL_TRUE_SEED)
            elif (
                isinstance(sub, ast.keyword)
                and sub.arg == "partial_success"
                and not (isinstance(sub.value, ast.Constant) and sub.value.value is False)
            ):
                found.add("degradation_state")
                found.add(_PARTIAL_TRUE_SEED)
            elif isinstance(sub, (ast.Assign, ast.AnnAssign)):
                value = getattr(sub, "value", None)
                targets = list(sub.targets) if isinstance(sub, ast.Assign) else [sub.target]
                if not (isinstance(value, ast.Constant) and value.value is False):
                    for target in targets:
                        if (
                            isinstance(target, ast.Subscript)
                            and isinstance(target.slice, ast.Constant)
                            and target.slice.value == "partial_success"
                        ):
                            found.add("degradation_state")
                            found.add(_PARTIAL_TRUE_SEED)
        text: str | None = None
        if isinstance(sub, ast.Name):
            text = sub.id
        elif isinstance(sub, ast.Attribute):
            text = sub.attr
        elif isinstance(sub, ast.keyword):
            text = sub.arg
        elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            text = sub.value
        if not text:
            continue
        for token in vocabulary:
            if _matches_token(text, token):
                found.add(token)
    return found


def _called_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
        elif isinstance(sub, ast.Name):
            # ``callback=_emit_x_json`` -- a bare reference is enough to
            # attribute the helper to this branch.
            names.add(sub.id)
    return names


def _has_nonzero_exit(node: ast.AST) -> bool:
    """True when ``node`` leaves the process with a non-zero status.

    ``ctx.exit(5)``, ``sys.exit(1)``, ``raise SystemExit(EXIT_GATE_FAILURE)``.
    A bare ``ctx.exit()`` or ``exit(0)`` is not a gate.
    """
    for sub in ast.walk(node):
        call: ast.Call | None = None
        if isinstance(sub, ast.Raise) and isinstance(sub.exc, ast.Call):
            func = sub.exc.func
            if isinstance(func, ast.Name) and func.id == "SystemExit":
                call = sub.exc
        elif isinstance(sub, ast.Call):
            func = sub.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in ("exit", "_exit"):
                call = sub
        if call is None or not call.args:
            continue
        arg = call.args[0]
        if isinstance(arg, ast.Constant):
            if isinstance(arg.value, int) and arg.value != 0:
                return True
        elif isinstance(arg, ast.Name) and arg.id.startswith("EXIT_"):
            return True
    return False


def _emits_output(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in _EMIT_FUNCS:
                return True
    return False


def _sarif_artifact_call(node: ast.AST) -> bool:
    """True when ``node`` passes data through a SARIF document builder."""
    return any("sarif" in name.lower() and name not in {"write_sarif"} for name in _called_names(node))


def _is_command(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for dec in func.decorator_list:
        node = dec.func if isinstance(dec, ast.Call) else dec
        attr = node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", "")
        if attr in ("command", "group"):
            return True
    return False


#: Class-B: two ``def``s sharing a name in one module. The call graph
#: resolves callees BY NAME, so the loser is never analysed at that call
#: site. Reported, not gated — see the Class-A/Class-B note in the module
#: docstring. Measured at HEAD: 28 shadowed definitions across 11 of 275
#: modules, which is why tainting on it would be a 4% false-partial rate on
#: a limit every file shares.
IMPRECISION_SHADOWED_DEF = "shadowed_definition"

#: Class B also covers the module boundary — the callee closure stops there
#: by design — and the shape of the mode predicates the partitioner
#: recognises. Neither gets a key in ``imprecision``: nothing here counts
#: them, and publishing an unmeasured limit as ``0`` would be the same
#: fabricated denominator this file exists to remove, one field over. They
#: are stated in the module docstring, where an unquantified bound belongs.


def _collect_functions(
    tree: ast.Module,
    budget: AnalysisBudget | None = None,
) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    funcs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    shadowed = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in funcs:
                shadowed += 1
                continue
            funcs[node.name] = node
    if budget is not None:
        budget.approximate(IMPRECISION_SHADOWED_DEF, shadowed)
    return funcs


def _expand(
    seeds: set[str],
    funcs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    budget: AnalysisBudget,
    limit: int | None = None,
) -> set[str]:
    """Transitive closure of in-module callees reachable from ``seeds``.

    ``seen`` is a subset of ``funcs``, so ``limit`` bounds nothing a normal
    module can reach — but when it does bite it drops callees from the
    closure, which narrows the ``supported`` mode set, which can drop a
    command below the two-mode threshold and return "no asymmetry here" for
    a command that was never compared. That silent path is what
    ``budget.hit`` closes: a truncated closure now taints the file.

    ``limit`` is read from the module global at CALL time rather than bound
    as a default, so a test can scale the threshold down instead of
    generating a 4000-function module to reach it. The mechanism under test
    is "the walk stopped early and the file did not report clean", which
    does not care what number stopped it.
    """
    limit = _EXPAND_LIMIT if limit is None else limit
    seen: set[str] = set()
    stack = [n for n in seeds if n in funcs]
    while stack:
        if len(seen) >= limit:
            budget.hit(CAP_CALLEE_CLOSURE)
            break
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        for callee in _called_names(funcs[name]):
            if callee in funcs and callee not in seen:
                stack.append(callee)
    return seen


def _collect_tokens(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    active: set[str],
    funcs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    n_supported: int,
    observed: dict[str, set[str]],
    observed_lines: dict[str, dict[str, set[int]]],
    vocabulary: tuple[str, ...],
    carried: dict[str, set[str]],
    budget: AnalysisBudget,
) -> None:
    """Attribute disclosure tokens in ``func`` to the modes that can see them.

    A mode OBSERVES a token only in code that is BRANCH-SPECIFIC — reached by
    a proper subset of the supported modes — or that emits directly.
    Appending to ``warnings_out`` in shared setup credits nobody: every
    branch can see the variable, and the defect is precisely that only one
    of them routes it to output.

    The walk follows in-module callees and re-partitions each one, because a
    shared emitter that takes ``json_mode`` as a PARAMETER and branches on it
    internally is a mode dispatcher too — crediting the whole helper to every
    caller mode is how this asymmetry stays invisible.

    That walk was recursive with a ``depth > 12`` cut-off, which is why a
    disclosure routed through a thirteenth helper was invisible AND the file
    still reported clean. The cut-off is gone rather than raised: ``visited``
    keys on ``(name, modes)`` and already makes the traversal terminating and
    complete, so the depth number was only ever guarding the Python stack —
    and an explicit worklist does not use the stack. The remaining bound is
    the count of distinct ``(name, modes)`` pairs, which is an upper bound the
    traversal cannot exceed rather than a budget it might; it is checked
    anyway, because an invariant that is merely believed is the state this
    module exists to remove.
    """
    visited: set[tuple[str, frozenset[str]]] = set()
    pending: list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, frozenset[str]]] = [(func, frozenset(active))]
    step_bound = len(funcs) * (2 ** len(MODES)) + len(MODES) + 1
    steps = 0

    while pending:
        current, modes = pending.pop()
        key = (current.name, modes)
        if not modes or key in visited:
            continue
        steps += 1
        if steps > step_bound:
            budget.hit(CAP_MODE_WALK)
            return
        visited.add(key)

        part = _Partition()
        part.walk(current.body, set(modes))

        owners: dict[int, int] = {}
        for mode in modes:
            for node in part.regions[mode]:
                owners[id(node)] = owners.get(id(node), 0) + 1

        # Pass 1 -- local aliases. A name bound to a token-bearing expression
        # stands in for the token for the rest of this mode's region. Walk into
        # conditionals as well: verdicts are commonly assigned in
        # ``if no_data: verdict = "No symbols..."`` and later emitted by every
        # mode. Treating only top-level Assign nodes made those honest prose
        # mirrors invisible and produced false positives.
        aliases: dict[str, dict[str, set[str]]] = {}
        for mode in modes:
            bound: dict[str, set[str]] = {}
            for node in part.regions[mode]:
                assignments = (
                    sub for sub in ast.walk(node) if isinstance(sub, (ast.Assign, ast.AnnAssign, ast.AugAssign))
                )
                for assignment in assignments:
                    value = getattr(assignment, "value", None)
                    if value is None:
                        continue
                    if assignment is node:
                        tokens = _observed_tokens(value, vocabulary, carried, bound)
                    else:
                        # Nested aliases are needed for conditional verdict
                        # prose, but must not turn an ordinary result into a
                        # disclosure merely because its producer accepted a
                        # ``warnings_out=...`` accumulator. Only propagate the
                        # semantic prose family here; explicit warning buckets
                        # still have to reach an emitter themselves.
                        tokens = _node_tokens(value, vocabulary) & {"degradation_state"}
                        for sub in ast.walk(value):
                            if isinstance(sub, ast.Name):
                                tokens |= bound.get(sub.id, set()) & {"degradation_state"}
                    if tokens:
                        for name in _assigned_names(assignment):
                            bound.setdefault(name, set()).update(tokens)
            aliases[mode] = bound

        callers: dict[str, set[str]] = {}
        for mode in modes:
            for node in part.regions[mode]:
                if owners[id(node)] < n_supported or _emits_output(node):
                    node_tokens = _observed_tokens(node, vocabulary, carried, aliases[mode])
                    if (
                        mode == "sarif"
                        and {"echo_text_warnings", "echo_text_empty_corpus"} & _called_names(node)
                        and not _sarif_artifact_call(node)
                    ):
                        # STDERR alongside a SARIF document is not retained by
                        # code-scanning consumers. Require the warning to enter
                        # the SARIF builder (normally as a runtime notification).
                        node_tokens -= set(vocabulary)
                    observed[mode] |= node_tokens
                    for token in node_tokens:
                        observed_lines[mode].setdefault(token, set()).add(getattr(node, "lineno", current.lineno))
                # The gate rule is judged on plain REACHABILITY: an exit in
                # shared code is symmetric, an exit only one branch can reach
                # is the py-types defect.
                if _has_nonzero_exit(node):
                    observed[mode].add(GATE_SIGNAL)
                    observed_lines[mode].setdefault(GATE_SIGNAL, set()).add(getattr(node, "lineno", current.lineno))
                for name in _called_names(node):
                    if name in funcs and name != current.name:
                        callers.setdefault(name, set()).add(mode)

        for name, called_by in callers.items():
            pending.append((funcs[name], frozenset(called_by)))


def analyze_command(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    funcs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    vocabulary: tuple[str, ...] = DISCLOSURE_TOKENS,
    budget: AnalysisBudget | None = None,
) -> list[dict[str, object]]:
    """Return the disclosure asymmetries in one click command function.

    ``budget`` is where every incomplete enumeration is recorded. Callers
    that pass one MUST consult it: ``[]`` from a run that hit a cap means
    "nothing was found in the part that was examined", which is not the same
    claim as "this command is symmetric" — and ``scan_file`` is what turns
    that difference into ``PARTIAL`` rather than ``CLEAN``.
    """
    budget = budget if budget is not None else AnalysisBudget()
    part = _Partition()
    part.walk(func.body, set(MODES))

    # Which modes exist at all in this command? A mode is real only when
    # some statement attributed to it emits output AND the mode is either
    # text (always) or actually named by a predicate somewhere.
    named: set[str] = {"text"}
    for name in _expand({func.name}, funcs, budget) | {func.name}:
        for sub in ast.walk(funcs[name]):
            if isinstance(sub, ast.If):
                mt = _classify_test(sub.test)
                if mt is not None:
                    named |= mt.modes

    supported: set[str] = set()
    for mode in MODES:
        if mode not in named:
            continue
        nodes = part.regions[mode]
        seeds: set[str] = set()
        for node in nodes:
            seeds |= _called_names(node)
        reachable = _expand(seeds, funcs, budget)
        if any(_emits_output(n) for n in nodes) or any(_emits_output(funcs[n]) for n in reachable):
            supported.add(mode)

    if len(supported) < 2:
        # Fewer than two output modes: there is no second branch to be
        # asymmetric WITH, so this is a genuine "nothing to compare", not a
        # truncation. It is only honest while the closure above was complete,
        # which is exactly what ``budget`` now records.
        return []

    observed: dict[str, set[str]] = {m: set() for m in supported}
    observed_lines: dict[str, dict[str, set[int]]] = {m: {} for m in supported}
    analysis_vocabulary = tuple(dict.fromkeys((*vocabulary, *_GATE_COMPENSATION_TOKENS)))
    _collect_tokens(
        func,
        supported,
        funcs,
        len(supported),
        observed,
        observed_lines,
        analysis_vocabulary,
        _carried_tokens(funcs, analysis_vocabulary, budget),
        budget,
    )

    violations: list[dict[str, object]] = []
    for token in (*vocabulary, GATE_SIGNAL):
        seeing = sorted(m for m in supported if token in observed[m])
        if token == "degradation_state" and _PARTIAL_TRUE_SEED not in observed.get("json", set()):
            # Text commonly contains words such as "failed" as a normal
            # result label. Only a JSON ``partial_success`` value that can
            # become true seeds this semantic family; prose supplies the
            # cross-format mirror but cannot create a violation on its own.
            continue
        blind = supported - set(seeing)
        if token == GATE_SIGNAL:
            blind = {
                mode
                for mode in blind
                if not any(compensation in observed[mode] for compensation in _GATE_COMPENSATION_TOKENS)
            }
        if seeing and blind:
            source_lines = [line for mode in seeing for line in observed_lines[mode].get(token, set())]
            violations.append(
                {
                    "command": func.name,
                    "token": token,
                    "observed_by": seeing,
                    "blind": sorted(blind),
                    "line": min(source_lines, default=func.lineno),
                }
            )
    return violations


# --------------------------------------------------------------------------
# Three-valued file outcomes (W1455)
# --------------------------------------------------------------------------

#: A file was read, parsed, analysed IN FULL, and found symmetric.
CLEAN = "clean"
#: A file was read, parsed, analysed, and found asymmetric.
VIOLATION = "violation"
#: A file could NOT be read, parsed, or analysed. This is the third value the
#: scanner used to collapse into ``[]`` — i.e. into CLEAN.
UNANALYZABLE = "unanalyzable"
#: A file whose analysis STOPPED EARLY — a cap, a budget, a round limit. The
#: fourth value, and the W1459 one: the file was read and parsed and no
#: asymmetry was found in the part that was examined, which is not a finding
#: about the part that was not. Distinct from UNANALYZABLE because the failure
#: is in the analysis's reach rather than in the file, and distinct from CLEAN
#: because a partial examination is not a clean bill of health.
PARTIAL = "partial"
#: A file the scan deliberately does not cover, recorded WITH a reason so the
#: coverage hole is a number in the report rather than an absence.
SKIPPED = "skipped"

EXIT_OK = 0
EXIT_VIOLATIONS = 1
#: The tree was not fully analysed: a file could not be read/parsed, OR a
#: file's analysis was truncated by a cap. Both mean the gate did not cover
#: what it claims to cover, which is one condition, so it is one exit code.
EXIT_UNANALYZABLE = 2
EXIT_SCANNER_BROKEN = 3

#: Directory of the planted defect the scanner must find on every run.
POSITIVE_CONTROL_DIR = pathlib.PurePosixPath("tests/fixtures/scanner_positive_controls/disclosure")

#: The sentinel: what the positive-control fixture is planted to produce.
#: A change to ``ast``, to the token vocabulary, or to the partitioner that
#: stops this from matching means the scanner has stopped detecting, and the
#: run is reported BROKEN instead of clean.
POSITIVE_CONTROL_SENTINEL: dict[str, object] = {
    "module": "cmd_sentinel_asymmetry.py",
    "command": "sentinel_disclosure_probe",
    "token": "warnings_out",
    "blind": ["text"],
    "observed_by": ["json"],
}
#: The other half of the control: the symmetric twin that must stay silent,
#: so "always fire" cannot satisfy the check.
POSITIVE_CONTROL_SILENT_MODULE = "cmd_sentinel_symmetric.py"


@dataclasses.dataclass(frozen=True)
class FileResult:
    """The outcome of ONE file. ``status`` is never inferred from emptiness.

    ``caps_hit`` is carried separately from ``status`` because a file can be
    BOTH: a truncated analysis that still found an asymmetry reports
    ``VIOLATION`` (the finding is real — truncation loses findings, it does
    not invent them) while ``caps_hit`` records that the rest of the file was
    never reached. Folding the two together would have to pick one, and
    picking either loses information the consumer needs.
    """

    module: str
    path: str
    status: str
    violations: list[dict[str, object]] = dataclasses.field(default_factory=list)
    sites: list[dict[str, object]] = dataclasses.field(default_factory=list)
    reason: str | None = None
    caps_hit: dict[str, int] = dataclasses.field(default_factory=dict)
    imprecision: dict[str, int] = dataclasses.field(default_factory=dict)

    @property
    def fully_analysed(self) -> bool:
        return not self.caps_hit

    def as_record(self) -> dict[str, object]:
        return {
            "module": self.module,
            "path": self.path,
            "status": self.status,
            "reason": self.reason,
            "caps_hit": dict(self.caps_hit),
        }


@dataclasses.dataclass(frozen=True)
class ControlResult:
    """Did the detector detect its own planted defect on this invocation?"""

    status: str  # "ok" | "broken"
    detail: str
    fixture: str

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def as_record(self) -> dict[str, object]:
        return {"status": self.status, "detail": self.detail, "fixture": self.fixture}


@dataclasses.dataclass(frozen=True)
class ScanReport:
    """Violations AND the denominator they were counted against.

    ``violations == []`` is only meaningful beside ``files_unanalyzable == 0``,
    ``files_capped == 0`` and ``positive_control.ok``; consumers must assert
    all four separately, which is why they are four fields and not one boolean.

    ``files_parsed`` is a count of FILES. It is deliberately not the headline
    number any more, because a file can be parsed and only partly analysed:
    ``files_capped`` is the completeness of the ANALYSIS, and reporting the
    first without the second is how a truncated scan came to look total.
    """

    violations: list[dict[str, object]]
    sites: list[dict[str, object]]
    files_parsed: int
    files_unanalyzable: int
    files_skipped: int
    unanalyzable: list[dict[str, object]]
    skipped: list[dict[str, object]]
    positive_control: ControlResult
    root: str
    #: Files whose analysis stopped early and found nothing — status PARTIAL.
    files_partial: int = 0
    #: Files whose analysis stopped early AT ALL, including those that still
    #: produced a violation. Always >= ``files_partial``.
    files_capped: int = 0
    #: Per-cap totals across the run: which bound truncated, and how often.
    cap_hits: dict[str, int] = dataclasses.field(default_factory=dict)
    #: The truncated files themselves, so the count is never the only evidence.
    capped: list[dict[str, object]] = dataclasses.field(default_factory=list)
    #: Class-B modelling limits, reported and NOT gated (see module docstring).
    imprecision: dict[str, int] = dataclasses.field(default_factory=dict)

    @property
    def files_seen(self) -> int:
        return self.files_parsed + self.files_unanalyzable

    @property
    def ok(self) -> bool:
        return (
            not self.violations and self.files_unanalyzable == 0 and self.files_capped == 0 and self.positive_control.ok
        )

    def summary(self) -> str:
        """The disclosure is the COUNT, never the silence."""
        return (
            f"checked {self.files_parsed} files, "
            f"{len(self.violations)} violations, "
            f"{self.files_unanalyzable} unanalyzable, "
            f"{self.files_capped} truncated, "
            f"positive control {'OK' if self.positive_control.ok else 'BROKEN'}"
        )

    def as_record(self) -> dict[str, object]:
        return {
            "root": self.root,
            "violations": self.violations,
            "files_parsed": self.files_parsed,
            "files_unanalyzable": self.files_unanalyzable,
            "files_partial": self.files_partial,
            "files_capped": self.files_capped,
            "files_skipped": self.files_skipped,
            "cap_hits": self.cap_hits,
            "capped": self.capped,
            "imprecision": self.imprecision,
            "unanalyzable": self.unanalyzable,
            "skipped": self.skipped,
            "positive_control": self.positive_control.as_record(),
            "summary": self.summary(),
        }


def _parse_source(path: pathlib.Path) -> tuple[ast.Module | None, str | None]:
    """Parse ``path``; on failure return the REASON rather than an empty result."""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"read failed: {type(exc).__name__}: {exc}"
    try:
        return ast.parse(source, filename=str(path)), None
    except (SyntaxError, ValueError, RecursionError) as exc:
        return None, f"parse failed: {type(exc).__name__}: {exc}"


def scan_file(path: pathlib.Path, vocabulary: tuple[str, ...] = DISCLOSURE_TOKENS) -> FileResult:
    """Analyse one module. NEVER returns an empty result for a file it failed on.

    Every exception is caught PER FILE and recorded as ``UNANALYZABLE`` with
    its reason — the walk keeps going, but the failure is carried into the
    report instead of being swallowed by a bare ``continue``.

    And every CAP is carried the same way. ``CLEAN`` is returned only when
    the analysis both found nothing and ran to completion; if any bound
    truncated it, the file is ``PARTIAL`` and names the bound. "No asymmetry
    in the 12 levels I looked at" is not "no asymmetry".
    """
    budget = AnalysisBudget()
    tree, reason = _parse_source(path)
    if tree is None:
        return FileResult(module=path.name, path=str(path), status=UNANALYZABLE, reason=reason)
    try:
        funcs = _collect_functions(tree, budget)
        violations: list[dict[str, object]] = []
        sites: list[dict[str, object]] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_command(node):
                sites.append({"module": path.name, "command": node.name, "line": node.lineno})
                for violation in analyze_command(node, funcs, vocabulary, budget):
                    violation["module"] = path.name
                    violations.append(violation)
    except Exception as exc:  # noqa: BLE001 — a scanner crash is UNANALYZABLE, not clean
        return FileResult(
            module=path.name,
            path=str(path),
            status=UNANALYZABLE,
            reason=f"analysis failed: {type(exc).__name__}: {exc}",
            caps_hit=dict(budget.caps_hit),
            imprecision=dict(budget.imprecision),
        )
    if violations:
        status = VIOLATION
    elif budget.complete:
        status = CLEAN
    else:
        status = PARTIAL
    return FileResult(
        module=path.name,
        path=str(path),
        status=status,
        violations=violations,
        sites=sites,
        reason=(f"analysis truncated: {budget.describe()}" if budget.caps_hit else None),
        caps_hit=dict(budget.caps_hit),
        imprecision=dict(budget.imprecision),
    )


def scan_files(commands_dir: pathlib.Path, vocabulary: tuple[str, ...] = DISCLOSURE_TOKENS) -> list[FileResult]:
    """Per-file outcomes for a directory, including the files it did not cover."""
    results: list[FileResult] = []
    try:
        candidates = sorted(commands_dir.glob("*.py"))
    except OSError as exc:
        return [
            FileResult(
                module=commands_dir.name,
                path=str(commands_dir),
                status=UNANALYZABLE,
                reason=f"directory listing failed: {type(exc).__name__}: {exc}",
            )
        ]
    for path in candidates:
        if not path.name.startswith("cmd_"):
            # Recorded, not ignored: a refactor that moves commands out of the
            # ``cmd_*.py`` glob shows up as a jump in ``files_skipped``.
            results.append(
                FileResult(
                    module=path.name,
                    path=str(path),
                    status=SKIPPED,
                    reason="outside the cmd_*.py glob",
                )
            )
            continue
        results.append(scan_file(path, vocabulary))
    results.extend(_subdirectory_skips(commands_dir))
    return results


def _subdirectory_skips(commands_dir: pathlib.Path) -> list[FileResult]:
    """Record the modules ONE DIRECTORY DOWN, which ``glob`` never reaches.

    ``glob("*.py")`` is one level deep, so ``commands/pr_analyze/rules.py``
    was in no bucket at all: not parsed, not unanalyzable, not skipped. A
    coverage hole with no number beside it is the same shape as a truncated
    walk that reports clean, so it gets a number. They are SKIPPED rather
    than UNANALYZABLE because none of them defines a click command today —
    if one ever does, this count moves and the reason says where to look.
    """
    out: list[FileResult] = []
    try:
        children = sorted(p for p in commands_dir.iterdir() if p.is_dir())
    except OSError:
        return out
    for child in children:
        if child.name == "__pycache__":
            continue
        try:
            nested = sorted(child.rglob("*.py"))
        except OSError:
            continue
        for path in nested:
            out.append(
                FileResult(
                    module=f"{child.name}/{path.name}",
                    path=str(path),
                    status=SKIPPED,
                    reason="below the command root; the scan globs one level only",
                )
            )
    return out


def _build_report(
    commands_dir: pathlib.Path,
    vocabulary: tuple[str, ...],
    control: ControlResult,
) -> ScanReport:
    results = scan_files(commands_dir, vocabulary)
    violations = [v for r in results for v in r.violations]
    violations.sort(key=lambda v: (str(v["module"]), str(v["command"]), str(v["token"])))
    sites = [s for r in results for s in r.sites]
    sites.sort(key=lambda s: (str(s["module"]), int(s["line"])))  # type: ignore[arg-type]
    unanalyzable = [r.as_record() for r in results if r.status == UNANALYZABLE]
    skipped = [r.as_record() for r in results if r.status == SKIPPED]
    capped = [r.as_record() for r in results if r.caps_hit]
    cap_hits: dict[str, int] = {}
    imprecision: dict[str, int] = {}
    for result in results:
        for cap, n in result.caps_hit.items():
            cap_hits[cap] = cap_hits.get(cap, 0) + n
        for kind, n in result.imprecision.items():
            imprecision[kind] = imprecision.get(kind, 0) + n
    return ScanReport(
        violations=violations,
        sites=sites,
        # PARTIAL files were read and parsed, so they belong in the parsed
        # count; what they are NOT is fully analysed, which is why
        # ``files_capped`` sits beside it rather than inside it.
        files_parsed=sum(1 for r in results if r.status in (CLEAN, VIOLATION, PARTIAL)),
        files_unanalyzable=len(unanalyzable),
        files_skipped=len(skipped),
        files_partial=sum(1 for r in results if r.status == PARTIAL),
        files_capped=len(capped),
        cap_hits=cap_hits,
        capped=capped,
        imprecision=imprecision,
        unanalyzable=unanalyzable,
        skipped=skipped,
        positive_control=control,
        root=str(commands_dir),
    )


# --------------------------------------------------------------------------
# Positive control
# --------------------------------------------------------------------------


def positive_control_dir(root: pathlib.Path | None = None) -> pathlib.Path:
    return (root or _repo_root()).joinpath(*POSITIVE_CONTROL_DIR.parts)


def run_positive_control(root: pathlib.Path | None = None) -> ControlResult:
    """Re-prove, on THIS invocation, that the detector still detects.

    Scans a fixture holding one planted asymmetry and its symmetric twin. The
    control passes only if the scanner fires on the first and stays silent on
    the second — an "always fire" or "never fire" regression fails it either
    way. Without this, a future ``ast`` change or a renamed node type turns
    the gate into a rubber stamp that still prints ``0 violations``.
    """
    fixture = positive_control_dir(root)
    if not fixture.is_dir():
        return ControlResult("broken", f"positive-control fixture directory missing: {fixture}", str(fixture))

    results = scan_files(fixture, DISCLOSURE_TOKENS)
    broken = [r for r in results if r.status == UNANALYZABLE]
    if broken:
        return ControlResult("broken", f"control fixture unanalyzable: {broken[0].reason}", str(fixture))

    truncated = [r for r in results if r.caps_hit]
    if truncated:
        # A control whose own analysis stopped early cannot certify that the
        # detector still detects: it proves the detector detects as far as it
        # looked. That is the claim this whole file exists to refuse.
        return ControlResult(
            "broken",
            f"control fixture analysis was truncated ({truncated[0].reason}) — "
            "a partially-analysed control proves nothing about the scan",
            str(fixture),
        )

    modules = {r.module for r in results if r.status in (CLEAN, VIOLATION, PARTIAL)}
    required = {str(POSITIVE_CONTROL_SENTINEL["module"]), POSITIVE_CONTROL_SILENT_MODULE}
    if not required <= modules:
        return ControlResult(
            "broken",
            f"control fixture incomplete: expected {sorted(required)}, parsed {sorted(modules)}",
            str(fixture),
        )

    noisy = [v for r in results if r.module == POSITIVE_CONTROL_SILENT_MODULE for v in r.violations]
    if noisy:
        return ControlResult(
            "broken",
            f"the symmetric control twin was flagged — the scanner is over-firing: {noisy}",
            str(fixture),
        )

    found = [v for r in results if r.module == str(POSITIVE_CONTROL_SENTINEL["module"]) for v in r.violations]
    match = [v for v in found if all(v.get(field) == expected for field, expected in POSITIVE_CONTROL_SENTINEL.items())]
    if not match:
        return ControlResult(
            "broken",
            "the planted sentinel asymmetry was NOT detected — a '0 violations' verdict "
            f"from this scanner is meaningless. expected {POSITIVE_CONTROL_SENTINEL}, got {found}",
            str(fixture),
        )
    return ControlResult(
        "ok",
        f"sentinel detected: {POSITIVE_CONTROL_SENTINEL['module']}::"
        f"{POSITIVE_CONTROL_SENTINEL['command']}::{POSITIVE_CONTROL_SENTINEL['token']} "
        f"blind={POSITIVE_CONTROL_SENTINEL['blind']}; symmetric twin silent",
        str(fixture),
    )


# --------------------------------------------------------------------------
# Public entry points
# --------------------------------------------------------------------------


def scan(
    commands_dir: pathlib.Path,
    vocabulary: tuple[str, ...] = DISCLOSURE_TOKENS,
    root: pathlib.Path | None = None,
) -> ScanReport:
    """Scan a directory and return violations WITH their denominator.

    The positive control runs on every invocation; ``report.ok`` is false when
    it fails even if no violation was found, because in that case "no
    violation" carries no information.
    """
    return _build_report(commands_dir, vocabulary, run_positive_control(root))


def enumerate_command_sites(commands_dir: pathlib.Path) -> ScanReport:
    """Every Click command the scan traverses, plus the files it could not read.

    Returns the full report rather than a bare site list: a module dropped by
    a parse failure used to disappear from here silently, which is how a
    coverage-proving enumeration came to under-report its own coverage.
    """
    return scan(commands_dir)


def violation_key(v: dict[str, object]) -> str:
    return f"{v['module']}::{v['command']}::{v['token']}"


# --------------------------------------------------------------------------
# The shrinking baseline
# --------------------------------------------------------------------------

#: Reason codes usable in the ratchet baseline. Each entry MUST name one.
#: They are deliberately few and deliberately specific: a code that would
#: fit any violation is not a reason, it is a shrug.
REASON_CODES: dict[str, str] = {
    "structured-envelope-instead-of-exit": (
        "DELIBERATE and pinned by a test: the JSON branch answers a "
        "resolution failure with a structured envelope (partial_success, "
        "state, error) and exit 0, because a non-zero exit strips the "
        "structured signal at the MCP wrapper -- see the Pattern-1B/1C note "
        "in cmd_diagnose.py and test_cmd_why_resolution.py's explicit "
        "assert exit_code == 0. The text branch has no envelope to carry the "
        "signal, so it uses the exit code instead. Not a defect; do not "
        "'fix' by making --json exit non-zero without changing those tests."
    ),
}


def derive_reason(v: dict[str, object]) -> str:
    """Name WHY a violation is grandfathered, or ``"TODO"`` if nothing fits.

    Keyed on the (token, blind-mode) pair rather than the token alone: a
    ``warnings_out`` that only the SARIF artifact misses is a different defect,
    with a different fix, from one the whole text branch misses — and a reason
    code that cannot tell them apart is not naming a reason.
    """
    token = str(v["token"])
    raw_blind = v["blind"]
    blind = [str(mode) for mode in raw_blind] if isinstance(raw_blind, list) else []
    if token == GATE_SIGNAL:
        return "structured-envelope-instead-of-exit"
    if blind == ["sarif"]:
        return "sarif-artifact-omits-disclosure"
    if token == "warnings_out":
        return "json-only-warnings-bucket"
    if token == "empty_corpus_state":
        return "json-only-empty-corpus-state"
    return "TODO"


#: Fields of a violation that the baseline records. ``line`` is deliberately
#: EXCLUDED: the ratchet compares defect identity, and a baseline that churns
#: every time an unrelated import moves is a baseline nobody re-reads.
_BASELINE_FIELDS = ("command", "token", "observed_by", "blind", "module")


def baseline_path(root: pathlib.Path | None = None) -> pathlib.Path:
    return (root or _repo_root()) / "tests" / "data" / "disclosure_asymmetry_baseline.json"


#: What the mark says when there is no previous file to carry a reason from.
_UNEXPLAINED_MARK = (
    "No rationale recorded. The high-water mark above is the count measured "
    "when this file was first generated; replace this text with why the mark "
    "is what it is before anyone is asked to trust it. A number in a ratchet "
    "file with no reason beside it is indistinguishable from someone raising "
    "it to make CI green."
)


def _carried_mark(count: int, root: pathlib.Path | None = None) -> tuple[int, str]:
    """Carry the recorded mark AND its rationale forward; never raise the mark.

    Regenerating must not be a way to launder a growth. If the tree now has
    more asymmetries than the mark allows, the emitted file trips
    ``test_baseline_only_ever_shrinks`` until someone raises the mark by hand
    and says why — which is the whole point of recording a mark.

    The note is carried for the same reason: a regeneration that silently
    dropped the justification would leave the number standing alone, which is
    the state this field exists to prevent.

    A baseline that is PRESENT but unreadable propagates its error instead of
    falling back to ``count``. Swallowing it here was the same defect a third
    time: the recorded mark would be replaced by whatever the current tree
    measures, so an unreadable ratchet file silently raises the ratchet —
    exactly the laundering this function's docstring promises to prevent.
    Only a genuinely ABSENT file is a first generation.

    A PRESENT and PARSEABLE file whose mark is missing or not a usable
    integer is the same laundering by a fourth route (W1471). ``isinstance``
    was the whole check, and every way of failing it fell back to ``count``:
    deleting the ``_high_water_mark`` key, or writing it as ``"68"``, ``68.0``
    or ``null``, silently reset the ceiling to today's count at exit 0 — while
    the *rationale* was carried forward intact, so the emitted file read as a
    mark of 2 explained by the prose for 68. That is strictly worse than the
    zero-byte file W1460 closed: that one at least refused. ``bool`` is
    rejected explicitly because ``isinstance(True, int)`` is true in Python,
    and a mark of ``True`` compares as 1 in ``test_baseline_only_ever_shrinks``
    — a ratchet pinned at one entry by a typo.

    The mark and its rationale are refused TOGETHER. A number carried forward
    without the reason beside it is the exact state ``_high_water_mark_note``
    exists to prevent, and ``_UNEXPLAINED_MARK`` is long enough to satisfy
    ``test_a_raised_high_water_mark_says_why``'s length floor — so silently
    substituting it would launder a dropped justification past the guard
    written to catch it.

    :raises ValueError: if the file is present and parseable but carries no
        usable mark or no rationale. ``emit_baseline`` turns that into a
        refusal that leaves the existing ratchet exactly as it found it.
    """
    try:
        recorded = json.loads(baseline_path(root).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return count, _UNEXPLAINED_MARK
    if not isinstance(recorded, dict):
        raise ValueError(f"the ratchet file is a JSON {type(recorded).__name__}, not an object")
    mark = recorded.get("_high_water_mark")
    note = recorded.get("_high_water_mark_note")
    if isinstance(mark, bool) or not isinstance(mark, int) or mark < 0:
        raise ValueError(
            f"the ratchet file carries no usable _high_water_mark (got {mark!r}); it must be a non-negative integer"
        )
    if not isinstance(note, str) or not note.strip():
        raise ValueError(
            f"the ratchet file records a _high_water_mark of {mark} with no "
            f"_high_water_mark_note (got {note!r}) to say why it is what it is"
        )
    return mark, note


def build_baseline(report: ScanReport, root: pathlib.Path | None = None) -> dict[str, object]:
    """The ratchet file: every live asymmetry, each naming why it is tolerated."""
    violations = [
        {**{field: v[field] for field in _BASELINE_FIELDS}, "reason": derive_reason(v)} for v in report.violations
    ]
    mark, mark_note = _carried_mark(len(violations), root)
    return {
        "_comment": (
            "W1331 ratchet baseline -- see tests/"
            "test_w1331_disclosure_branch_symmetry.py. Every entry is a "
            "command that routes a disclosure signal to one output "
            "branch and not another, so a consumer of the other branch "
            "reads a degraded run as a clean one. This list may only "
            "SHRINK: the test fails both on a new violation and on a "
            "stale entry. Every entry names a reason code from "
            "_reason_codes; adding a code, or an entry, is a reviewable "
            "diff by construction."
        ),
        # NO REDIRECT. ``--baseline`` rewrites this file in place and prints
        # nothing to stdout; ``> tests/data/disclosure_asymmetry_baseline.json``
        # truncates the file in the shell before the scanner can read the mark
        # below out of it. See the W1460 note in the module docstring.
        "_regenerate": "python scripts/scan_disclosure_asymmetry.py --baseline",
        "_high_water_mark": mark,
        "_high_water_mark_note": mark_note,
        "_reason_codes": REASON_CODES,
        "violations": violations,
    }


def write_baseline(payload: dict[str, object], root: pathlib.Path | None = None) -> pathlib.Path:
    """Write the ratchet file ATOMICALLY, and return where it went.

    Via a sibling temp file and ``os.replace``, so the ratchet on disk is
    either the whole old file or the whole new one. A plain ``open(..., "w")``
    truncates first and would leave a zero-byte ratchet behind on a crash, a
    full disk, or a kill — the same lost high-water mark the shell redirect
    used to cause, just by a slower route.

    Sibling rather than ``tempfile.gettempdir()`` because ``os.replace`` is
    only atomic within a filesystem.

    ``newline="\\n"`` because this file is committed. Text-mode writes on
    Windows translate to CRLF, which would rewrite all 62 lines of the ratchet
    on every regeneration from a Windows checkout — a diff nobody reads is a
    diff nobody reviews, and the ratchet's whole value is that its diff is
    reviewable.
    """
    target = baseline_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
        os.replace(tmp, target)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    return target


def emit_baseline(report: ScanReport, root: pathlib.Path | None = None) -> int:
    """Regenerate the ratchet file in place; return the process exit code.

    NOTHING is written to stdout. The ratchet used to be printed and the
    caller told to redirect it over the file, which truncated that file in the
    shell before this process could read the high-water mark out of it — see
    the W1460 note in the module docstring. Owning the write is what removes
    the redirect from the contract; there is no ``--stdout`` escape hatch,
    because an escape hatch is the trap with an extra step.

    Every refusal below happens BEFORE ``write_baseline`` is called, so a
    refused regeneration leaves the existing ratchet exactly as it found it.
    """
    # The denominator is checked BEFORE the file is emitted: a baseline
    # regenerated from a scan that could not read part of the tree would
    # silently record "fixed" for every module it failed to parse.
    if not report.positive_control.ok:
        print(f"REFUSING to emit a baseline: {report.positive_control.detail}", file=sys.stderr)
        return EXIT_SCANNER_BROKEN
    if report.files_parsed == 0:
        print("REFUSING to emit a baseline: zero files parsed is zero coverage", file=sys.stderr)
        return EXIT_SCANNER_BROKEN
    if report.files_unanalyzable:
        for entry in report.unanalyzable:
            print(f"UNANALYZABLE {entry['module']}: {entry['reason']}", file=sys.stderr)
        print("REFUSING to emit a baseline from a partially-analysed tree", file=sys.stderr)
        return EXIT_UNANALYZABLE
    if report.files_capped:
        # Same refusal, one level in. A baseline built from truncated
        # analysis records "fixed" for every asymmetry the truncation hid,
        # and the ratchet then rejects that asymmetry FOREVER as a new
        # violation the moment the walk reaches it again.
        for entry in report.capped:
            print(f"TRUNCATED {entry['module']}: {entry['reason']}", file=sys.stderr)
        print(
            f"REFUSING to emit a baseline: {report.files_capped} file(s) were only "
            f"partially analysed ({report.cap_hits})",
            file=sys.stderr,
        )
        return EXIT_UNANALYZABLE
    try:
        payload = build_baseline(report, root)
    except (OSError, ValueError) as exc:
        # A ratchet file that is PRESENT but unreadable — or readable but
        # carrying no usable mark (W1471) — is not a first generation.
        # Recovery is named here rather than left as an exercise, because the
        # historical cause of this state was following the documented
        # instruction.
        print(
            f"REFUSING to emit a baseline: the existing ratchet file could not be read, "
            f"or carries no usable high-water mark "
            f"({type(exc).__name__}: {exc}); regenerating over it would replace its "
            "high-water mark with today's count. Restore it first — "
            f"git checkout -- {baseline_path(root)} — and re-run WITHOUT a shell "
            "redirect; --baseline writes the file itself.",
            file=sys.stderr,
        )
        return EXIT_UNANALYZABLE
    try:
        target = write_baseline(payload, root)
    except OSError as exc:
        print(f"REFUSING to emit a baseline: could not write it ({type(exc).__name__}: {exc})", file=sys.stderr)
        return EXIT_UNANALYZABLE
    print(
        f"wrote {target}: {len(payload['violations'])} entries, high-water mark {payload['_high_water_mark']}",
        file=sys.stderr,
    )  # type: ignore[arg-type]
    print(report.summary(), file=sys.stderr)
    return EXIT_OK


def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", action="store_true", help="human-readable report")
    parser.add_argument(
        "--baseline",
        action="store_true",
        help=(
            "rewrite tests/data/disclosure_asymmetry_baseline.json IN PLACE. "
            "Do NOT redirect: nothing goes to stdout, and a redirect onto the "
            "ratchet truncates it before the high-water mark can be read back."
        ),
    )
    parser.add_argument(
        "--include-schema-keys",
        action="store_true",
        help="also measure raw json_envelope schema-key spelling (semantic partial_success=true is always gated)",
    )
    parser.add_argument(
        "--root",
        default=None,
        help=(
            "directory of command modules to scan (default: src/roam/commands). "
            "Exists so the exit-code contract can be driven end to end over a "
            "planted tree — a gate whose exit codes are only ever asserted "
            "against the repository it ships in is a gate nobody has tested."
        ),
    )
    args = parser.parse_args(argv)

    vocabulary = DISCLOSURE_TOKENS + (SCHEMA_TOKENS if args.include_schema_keys else ())
    commands_dir = pathlib.Path(args.root) if args.root else _repo_root() / "src" / "roam" / "commands"
    report = scan(commands_dir, vocabulary)

    if args.baseline:
        return emit_baseline(report)

    if args.text:
        for v in report.violations:
            print(
                f"{v['module']}:{v['line']}::{v['command']}: "
                f"{v['token']} seen by {v['observed_by']}, blind: {v['blind']}"
            )
        disclosures = [v for v in report.violations if v["token"] != GATE_SIGNAL]
        gates = [v for v in report.violations if v["token"] == GATE_SIGNAL]
        print(
            f"\n{len(disclosures)} disclosure asymmetries across "
            f"{len({v['module'] for v in disclosures})} modules; "
            f"{len(gates)} separately-tested exit-gate reachability differences"
        )
        print(report.summary())
        print(f"skipped {report.files_skipped} file(s) outside the one-level cmd_*.py glob")
        print(f"analysis caps hit: {report.cap_hits or 'none'}")
        print(f"modelling limits (reported, not gated): {report.imprecision or 'none'}")
        for entry in report.unanalyzable:
            print(f"UNANALYZABLE {entry['module']}: {entry['reason']}")
        for entry in report.capped:
            print(f"TRUNCATED {entry['module']}: {entry['reason']}")
        if not report.positive_control.ok:
            print(f"POSITIVE CONTROL BROKEN: {report.positive_control.detail}")
    else:
        print(json.dumps(report.as_record(), indent=2))

    if not report.positive_control.ok:
        return EXIT_SCANNER_BROKEN
    if report.files_parsed == 0:
        # Zero files parsed is not zero violations; it is zero coverage.
        return EXIT_SCANNER_BROKEN
    if report.files_unanalyzable:
        # Cannot-analyse is a failure OF THE GATE, not a pass.
        return EXIT_UNANALYZABLE
    if report.files_capped:
        # Analysed-in-part is the same failure with a smaller radius: the
        # gate did not cover what it says it covered. Ranked ABOVE
        # EXIT_VIOLATIONS because a truncated run's violation list is a
        # floor, and reporting the floor as the total is the defect.
        return EXIT_UNANALYZABLE
    if report.violations:
        return EXIT_VIOLATIONS
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
