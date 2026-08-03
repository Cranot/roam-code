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

Exit codes: 0 clean, 1 violations, 2 unanalyzable files, 3 the scanner
cannot be trusted (positive control failed, or nothing was parsed at all).
The test consuming this scanner has a zero baseline: every live violation
fails.  There is no command allowlist to update.
"""

from __future__ import annotations

import argparse
import ast
import dataclasses
import json
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
    "degradation_state",
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
    """
    returned: dict[str, set[str]] = {}
    for name, body in funcs.items():
        tokens: set[str] = set()
        for sub in ast.walk(body):
            if isinstance(sub, ast.Return) and sub.value is not None:
                tokens |= _node_tokens(sub.value, vocabulary)
        returned[name] = tokens
    for _ in range(3):  # shallow fixpoint; return chains here are 1-2 deep
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
            break
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


def _collect_functions(tree: ast.Module) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    funcs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.setdefault(node.name, node)
    return funcs


def _expand(
    seeds: set[str],
    funcs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    limit: int = 4000,
) -> set[str]:
    """Transitive closure of in-module callees reachable from ``seeds``."""
    seen: set[str] = set()
    stack = [n for n in seeds if n in funcs]
    while stack and len(seen) < limit:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        for callee in _called_names(funcs[name]):
            if callee in funcs and callee not in seen:
                stack.append(callee)
    return seen


_MAX_DEPTH = 12


def _collect_tokens(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    active: set[str],
    funcs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    n_supported: int,
    observed: dict[str, set[str]],
    observed_lines: dict[str, dict[str, set[int]]],
    vocabulary: tuple[str, ...],
    carried: dict[str, set[str]],
    visited: set[tuple[str, frozenset[str]]],
    depth: int,
) -> None:
    """Attribute disclosure tokens in ``func`` to the modes that can see them.

    A mode OBSERVES a token only in code that is BRANCH-SPECIFIC — reached by
    a proper subset of the supported modes — or that emits directly.
    Appending to ``warnings_out`` in shared setup credits nobody: every
    branch can see the variable, and the defect is precisely that only one
    of them routes it to output.

    The walk recurses into in-module callees and re-partitions each one,
    because a shared emitter that takes ``json_mode`` as a PARAMETER and
    branches on it internally is a mode dispatcher too — crediting the whole
    helper to every caller mode is how this asymmetry stays invisible.
    """
    key = (func.name, frozenset(active))
    if key in visited or depth > _MAX_DEPTH or not active:
        return
    visited.add(key)

    part = _Partition()
    part.walk(func.body, set(active))

    owners: dict[int, int] = {}
    for mode in active:
        for node in part.regions[mode]:
            owners[id(node)] = owners.get(id(node), 0) + 1

    # Pass 1 -- local aliases. A name bound to a token-bearing expression
    # stands in for the token for the rest of this mode's region. Walk into
    # conditionals as well: verdicts are commonly assigned in
    # ``if no_data: verdict = "No symbols..."`` and later emitted by every
    # mode. Treating only top-level Assign nodes made those honest prose
    # mirrors invisible and produced false positives.
    aliases: dict[str, dict[str, set[str]]] = {}
    for mode in active:
        bound: dict[str, set[str]] = {}
        for node in part.regions[mode]:
            assignments = (sub for sub in ast.walk(node) if isinstance(sub, (ast.Assign, ast.AnnAssign, ast.AugAssign)))
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
    for mode in active:
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
                    observed_lines[mode].setdefault(token, set()).add(getattr(node, "lineno", func.lineno))
            # The gate rule is judged on plain REACHABILITY: an exit in
            # shared code is symmetric, an exit only one branch can reach
            # is the py-types defect.
            if _has_nonzero_exit(node):
                observed[mode].add(GATE_SIGNAL)
                observed_lines[mode].setdefault(GATE_SIGNAL, set()).add(getattr(node, "lineno", func.lineno))
            for name in _called_names(node):
                if name in funcs and name != func.name:
                    callers.setdefault(name, set()).add(mode)

    for name, modes in callers.items():
        _collect_tokens(
            funcs[name],
            modes,
            funcs,
            n_supported,
            observed,
            observed_lines,
            vocabulary,
            carried,
            visited,
            depth + 1,
        )


def analyze_command(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    funcs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    vocabulary: tuple[str, ...] = DISCLOSURE_TOKENS,
) -> list[dict[str, object]]:
    """Return the disclosure asymmetries in one click command function."""
    part = _Partition()
    part.walk(func.body, set(MODES))

    # Which modes exist at all in this command? A mode is real only when
    # some statement attributed to it emits output AND the mode is either
    # text (always) or actually named by a predicate somewhere.
    named: set[str] = {"text"}
    for name in _expand({func.name}, funcs) | {func.name}:
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
        reachable = _expand(seeds, funcs)
        if any(_emits_output(n) for n in nodes) or any(_emits_output(funcs[n]) for n in reachable):
            supported.add(mode)

    if len(supported) < 2:
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
        _carried_tokens(funcs, analysis_vocabulary),
        set(),
        0,
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

#: A file was read, parsed, analysed, and found symmetric.
CLEAN = "clean"
#: A file was read, parsed, analysed, and found asymmetric.
VIOLATION = "violation"
#: A file could NOT be read, parsed, or analysed. This is the third value the
#: scanner used to collapse into ``[]`` — i.e. into CLEAN.
UNANALYZABLE = "unanalyzable"
#: A file the scan deliberately does not cover, recorded WITH a reason so the
#: coverage hole is a number in the report rather than an absence.
SKIPPED = "skipped"

EXIT_OK = 0
EXIT_VIOLATIONS = 1
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
    """The outcome of ONE file. ``status`` is never inferred from emptiness."""

    module: str
    path: str
    status: str
    violations: list[dict[str, object]] = dataclasses.field(default_factory=list)
    sites: list[dict[str, object]] = dataclasses.field(default_factory=list)
    reason: str | None = None

    def as_record(self) -> dict[str, object]:
        return {"module": self.module, "path": self.path, "status": self.status, "reason": self.reason}


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

    ``violations == []`` is only meaningful beside ``files_unanalyzable == 0``
    and ``positive_control.ok``; consumers must assert all three separately,
    which is why they are three fields and not one boolean.
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

    @property
    def files_seen(self) -> int:
        return self.files_parsed + self.files_unanalyzable

    @property
    def ok(self) -> bool:
        return not self.violations and self.files_unanalyzable == 0 and self.positive_control.ok

    def summary(self) -> str:
        """The disclosure is the COUNT, never the silence."""
        return (
            f"checked {self.files_parsed} files, "
            f"{len(self.violations)} violations, "
            f"{self.files_unanalyzable} unanalyzable, "
            f"positive control {'OK' if self.positive_control.ok else 'BROKEN'}"
        )

    def as_record(self) -> dict[str, object]:
        return {
            "root": self.root,
            "violations": self.violations,
            "files_parsed": self.files_parsed,
            "files_unanalyzable": self.files_unanalyzable,
            "files_skipped": self.files_skipped,
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
    """
    tree, reason = _parse_source(path)
    if tree is None:
        return FileResult(module=path.name, path=str(path), status=UNANALYZABLE, reason=reason)
    try:
        funcs = _collect_functions(tree)
        violations: list[dict[str, object]] = []
        sites: list[dict[str, object]] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_command(node):
                sites.append({"module": path.name, "command": node.name, "line": node.lineno})
                for violation in analyze_command(node, funcs, vocabulary):
                    violation["module"] = path.name
                    violations.append(violation)
    except Exception as exc:  # noqa: BLE001 — a scanner crash is UNANALYZABLE, not clean
        return FileResult(
            module=path.name,
            path=str(path),
            status=UNANALYZABLE,
            reason=f"analysis failed: {type(exc).__name__}: {exc}",
        )
    return FileResult(
        module=path.name,
        path=str(path),
        status=VIOLATION if violations else CLEAN,
        violations=violations,
        sites=sites,
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
    return results


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
    return ScanReport(
        violations=violations,
        sites=sites,
        files_parsed=sum(1 for r in results if r.status in (CLEAN, VIOLATION)),
        files_unanalyzable=len(unanalyzable),
        files_skipped=len(skipped),
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

    modules = {r.module for r in results if r.status in (CLEAN, VIOLATION)}
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


def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", action="store_true", help="human-readable report")
    parser.add_argument(
        "--include-schema-keys",
        action="store_true",
        help="also measure raw json_envelope schema-key spelling (semantic partial_success=true is always gated)",
    )
    args = parser.parse_args(argv)

    vocabulary = DISCLOSURE_TOKENS + (SCHEMA_TOKENS if args.include_schema_keys else ())
    report = scan(_repo_root() / "src" / "roam" / "commands", vocabulary)

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
        print(f"skipped {report.files_skipped} file(s) outside the cmd_*.py glob")
        for entry in report.unanalyzable:
            print(f"UNANALYZABLE {entry['module']}: {entry['reason']}")
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
    if report.violations:
        return EXIT_VIOLATIONS
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
