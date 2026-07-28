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

A violation is: mode A observes disclosure token T and supported mode B
does not.

USAGE
-----
    python scripts/scan_disclosure_asymmetry.py            # raw JSON
    python scripts/scan_disclosure_asymmetry.py --text     # human report
    python scripts/scan_disclosure_asymmetry.py --baseline # ratchet file

``--baseline`` regenerates the file consumed by
``tests/test_w1331_disclosure_branch_symmetry.py``. Each entry names a
reason code from ``REASON_CODES``; a token with no mapping emits
``"TODO"``, which that test rejects. Exempting a command is a reviewable
diff by construction — the guard exists precisely because the fix keeps
getting written into the branch that is already under test.
"""

from __future__ import annotations

import argparse
import ast
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
    "failed_checks",
)

#: ``partial_success`` is deliberately NOT enforced. It is a key of the
#: shared ``json_envelope`` schema, so every JSON branch in the tree carries
#: it by construction and the check degenerates into "the text branch is not
#: JSON" — 164 hits, none of them actionable, and the only way to satisfy it
#: would be to print the word ``partial_success`` at a human. The signals
#: above are different in kind: each is a value the command COMPUTES from a
#: failure it just survived, so routing it to one branch and not another is
#: a real, fixable information loss. Measure the schema-key spread with
#: ``--include-schema-keys``; do not gate on it.
SCHEMA_TOKENS: tuple[str, ...] = ("partial_success",)

#: Pseudo-token for the second rule: a NON-ZERO exit (a CI gate) that only
#: some output modes can reach. This is the ``py-types`` defect verbatim —
#: ``--ci --min-coverage 90`` at 0% coverage exited 5 in text and 0 in both
#: --json and --sarif, i.e. the two modes CI actually consumes passed on the
#: identical number, because the gate sat after their early returns.
#: Unlike the tokens above this is judged on plain REACHABILITY, not on
#: branch-specific placement: an exit in shared code is symmetric by
#: construction, and that is exactly what the fix looks like.
GATE_SIGNAL = "nonzero_exit"

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
        if node.id in ("sarif_mode", "_sarif_mode"):
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
    # stands in for the token for the rest of this mode's region.
    aliases: dict[str, dict[str, set[str]]] = {}
    for mode in active:
        bound: dict[str, set[str]] = {}
        for node in part.regions[mode]:
            if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                continue
            value = getattr(node, "value", None)
            if value is None:
                continue
            tokens = _observed_tokens(value, vocabulary, carried, bound)
            if tokens:
                for name in _assigned_names(node):
                    bound.setdefault(name, set()).update(tokens)
        aliases[mode] = bound

    callers: dict[str, set[str]] = {}
    for mode in active:
        for node in part.regions[mode]:
            if owners[id(node)] < n_supported or _emits_output(node):
                observed[mode] |= _observed_tokens(node, vocabulary, carried, aliases[mode])
            # The gate rule is judged on plain REACHABILITY: an exit in
            # shared code is symmetric, an exit only one branch can reach
            # is the py-types defect.
            if _has_nonzero_exit(node):
                observed[mode].add(GATE_SIGNAL)
            for name in _called_names(node):
                if name in funcs and name != func.name:
                    callers.setdefault(name, set()).add(mode)

    for name, modes in callers.items():
        _collect_tokens(funcs[name], modes, funcs, n_supported, observed, vocabulary, carried, visited, depth + 1)


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
    _collect_tokens(
        func,
        supported,
        funcs,
        len(supported),
        observed,
        vocabulary,
        _carried_tokens(funcs, vocabulary),
        set(),
        0,
    )

    violations: list[dict[str, object]] = []
    for token in (*vocabulary, GATE_SIGNAL):
        seeing = sorted(m for m in supported if token in observed[m])
        if seeing and len(seeing) < len(supported):
            violations.append(
                {
                    "command": func.name,
                    "token": token,
                    "observed_by": seeing,
                    "blind": sorted(supported - set(seeing)),
                }
            )
    return violations


def scan_file(path: pathlib.Path, vocabulary: tuple[str, ...] = DISCLOSURE_TOKENS) -> list[dict[str, object]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []
    funcs = _collect_functions(tree)
    out: list[dict[str, object]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_command(node):
            for violation in analyze_command(node, funcs, vocabulary):
                violation["module"] = path.name
                out.append(violation)
    return out


def scan(commands_dir: pathlib.Path, vocabulary: tuple[str, ...] = DISCLOSURE_TOKENS) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for path in sorted(commands_dir.glob("cmd_*.py")):
        results.extend(scan_file(path, vocabulary))
    results.sort(key=lambda v: (str(v["module"]), str(v["command"]), str(v["token"])))
    return results


def violation_key(v: dict[str, object]) -> str:
    return f"{v['module']}::{v['command']}::{v['token']}"


#: Reason codes usable in the ratchet baseline. Each entry MUST name one.
#: They are deliberately few and deliberately specific: a code that would
#: fit any violation is not a reason, it is a shrug.
REASON_CODES: dict[str, str] = {
    "json-only-warnings-bucket": (
        "The W607 warnings_out bucket is threaded into the JSON envelope only. "
        "A human running the command without --json, and a CI job consuming "
        "--sarif, cannot see that a substrate call failed and was floored. "
        "Fix template: cmd_understand.py -- echo '# warning: <marker>' to "
        "STDERR in the non-JSON tails, which leaves stdout byte-identical."
    ),
    "json-only-empty-corpus-state": (
        "empty_corpus_state() distinguishes 'nothing indexed' from 'nothing "
        "found'; only the JSON branch renders the distinction. The text and "
        "SARIF branches print the same thing for both."
    ),
    "json-only-failed-checks": (
        "The list of checks that errored is carried in the JSON envelope only, "
        "so the text verdict reads clean even when checks did not run."
    ),
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

_DERIVED_REASON: dict[str, str] = {
    "warnings_out": "json-only-warnings-bucket",
    "empty_corpus_state": "json-only-empty-corpus-state",
    "failed_checks": "json-only-failed-checks",
    GATE_SIGNAL: "structured-envelope-instead-of-exit",
}


def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", action="store_true", help="human-readable report")
    parser.add_argument("--baseline", action="store_true", help="emit a baseline skeleton")
    parser.add_argument(
        "--include-schema-keys",
        action="store_true",
        help="also measure json_envelope schema keys (partial_success) -- reporting only, never gated",
    )
    args = parser.parse_args(argv)

    vocabulary = DISCLOSURE_TOKENS + (SCHEMA_TOKENS if args.include_schema_keys else ())
    results = scan(_repo_root() / "src" / "roam" / "commands", vocabulary)
    if args.baseline:
        print(
            json.dumps(
                {
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
                    "_regenerate": "python scripts/scan_disclosure_asymmetry.py --baseline",
                    "_reason_codes": REASON_CODES,
                    "violations": [{**v, "reason": _DERIVED_REASON.get(str(v["token"]), "TODO")} for v in results],
                },
                indent=2,
            )
        )
        return 0
    if args.text:
        for v in results:
            print(f"{v['module']}::{v['command']}: {v['token']} seen by {v['observed_by']}, blind: {v['blind']}")
        print(f"\n{len(results)} asymmetries across {len({v['module'] for v in results})} modules")
        return 0
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
