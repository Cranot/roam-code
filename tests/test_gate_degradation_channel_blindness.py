"""No output channel may be blind to a degradation another channel gates on.

THE SHAPE
---------
The live parity sweep in ``tests/test_gate_channel_exit_parity.py`` can only
catch a divergence it can INDUCE. Some degradations cannot be produced from a
test fixture at all -- a wedged git, a permission-denied read, a disk-full
partial write -- and a command that drops the ``or scan_incomplete`` term from
one channel's gate stays green in every scenario anyone can build.

This is the static half. It looks for exactly one shape, the one
``roam ignore-drift`` shipped::

    if json_mode:
        ...
        if fail_on_found and (violations or scan_incomplete):   # carries it
            raise GateFailureError(verdict)
        return
    ...
    if fail_on_found and violations:                            # blind to it
        raise GateFailureError(verdict)

A degradation name appears in the guard of a gate exit in ONE output-mode
region, that region's siblings also carry gate exits, and none of theirs
mentions it. The command has decided that condition is grounds for refusing
and then, in another channel, refuses to notice it.

WHY THIS RULE AND NOT A GENERAL GUARD-EQUALITY RULE
---------------------------------------------------
The obvious lint -- "every channel's gate guard must be textually identical"
-- was prototyped and rejected: it flags ~8 commands of which ~7 are
equivalent under normalisation (``ctx.exit(5 if failed else 0)`` versus
``if failed: ctx.exit(5)``, a summary dict re-read from the same envelope, and
so on). A guard with 7 hand-written exemptions is a guard nobody trusts, and
its exemption list is the thing that rots. This rule instead keys on the
DEGRADATION VOCABULARY, and it is measured at zero false positives across the
whole command surface.

The existing W1331 scanner (``scripts/scan_disclosure_asymmetry.py``) does not
cover this: its Rule 2 asks whether a non-zero exit is REACHABLE in each mode,
and pre-fix ``ignore-drift`` had one in both. It cannot see that the two exits
are guarded by different conditions, which is why it passed the seed cleanly.

WHAT THIS DOES NOT PROVE
------------------------
* It reads the vocabulary in ``_DEGRADATION_TOKENS``. A command that names its
  degradation something outside that list is invisible here.
* It finds ASYMMETRY, not blindness. A command that ignores a degradation in
  EVERY channel is symmetric and passes -- that is the class Law 2 of the live
  sweep exists for, and neither test closes it for a command that never
  publishes the signal at all.
* It models only the ``if json_mode:`` / ``if sarif:`` dispatch shape. A
  command that selects its channel some other way is not partitioned and its
  exits all land in every mode, where they compare equal.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

from roam.cli import _COMMANDS

#: Names that mean "this result is not a full measurement". Substring match,
#: so ``files_unreadable`` matches ``unreadable`` and ``_scan_incomplete``
#: matches ``scan_incomplete``.
_DEGRADATION_TOKENS: tuple[str, ...] = (
    "scan_incomplete",
    "partial_success",
    "unanalyzable",
    "empty_corpus",
    "incomplete",
    "unreadable",
    "undiscoverable",
    "not_measured",
    "degraded",
    "vacuous",
    "blind",
    "skipped",
)

_MODES: tuple[str, ...] = ("json", "sarif", "text")

#: Exceptions whose raise IS a gate refusal.
_GATE_EXCEPTIONS: frozenset[str] = frozenset({"GateFailureError", "SystemExit"})


# ---------------------------------------------------------------------------
# Mode-region partitioning
# ---------------------------------------------------------------------------


def _mode_test(test: ast.expr) -> tuple[str, set[str]] | None:
    """Resolve ``if <test>:`` to the output modes its body serves."""
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        inner = _mode_test(test.operand)
        return None if inner is None else ("not", inner[1])
    if isinstance(test, ast.Name):
        if test.id in ("json_mode", "_json_mode"):
            return ("is", {"json"})
        if test.id in ("sarif", "sarif_mode", "_sarif_mode", "sarif_path"):
            return ("is", {"sarif"})
        return None
    if isinstance(test, ast.Call):
        func = test.func
        if isinstance(func, ast.Attribute) and func.attr == "get" and test.args:
            arg = test.args[0]
            if isinstance(arg, ast.Constant) and arg.value in ("json", "sarif"):
                return ("is", {str(arg.value)})
        return None
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or):
        parts = [_mode_test(value) for value in test.values]
        if all(part is not None and part[0] == "is" for part in parts):
            merged: set[str] = set()
            for part in parts:
                merged |= part[1]  # type: ignore[index]
            return ("is", merged)
    return None


def _is_gate_exit(node: ast.stmt) -> bool:
    if isinstance(node, ast.Raise) and node.exc is not None:
        exc = node.exc
        if isinstance(exc, ast.Call):
            name = getattr(exc.func, "id", None) or getattr(exc.func, "attr", None)
        else:
            name = getattr(exc, "id", None) or getattr(exc, "attr", None)
        return name in _GATE_EXCEPTIONS
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
        call = node.value
        name = getattr(call.func, "attr", None) or getattr(call.func, "id", None)
        if name in ("exit", "_exit") and call.args:
            first = call.args[0]
            return isinstance(first, ast.Constant) and isinstance(first.value, int) and first.value != 0
    return False


def _terminates(body: list[ast.stmt]) -> bool:
    """True when control definitely leaves the function at the end of ``body``.

    Modelling this is load-bearing. ``if json_mode: ...; return`` creates a
    text-only region with no ``else``, and that early return IS the seed's
    shape -- a classifier that only looks at if/else bodies scores both
    branches as shared and reports the defect clean.
    """
    if not body:
        return False
    last = body[-1]
    if isinstance(last, (ast.Return, ast.Raise, ast.Continue, ast.Break)):
        return True
    if isinstance(last, ast.Expr) and isinstance(last.value, ast.Call):
        func = last.value.func
        if getattr(func, "attr", None) == "exit" or getattr(func, "id", None) == "exit":
            return True
    if isinstance(last, ast.If) and last.orelse:
        return _terminates(last.body) and _terminates(last.orelse)
    return False


def _guard_names(test: ast.expr) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(test):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            names.add(node.value)
    return names


def _gate_guards_by_mode(func: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, list[set[str]]]:
    """For each output mode, the names in the IMMEDIATE guard of each gate exit.

    Immediate, not the whole enclosing stack: a text branch legitimately nests
    its gate inside an ``if scan_incomplete:`` DISCLOSURE block, and charging
    that block's condition to the gate would flag the corrected code.
    """
    found: dict[str, list[set[str]]] = {mode: [] for mode in _MODES}

    def walk(stmts: list[ast.stmt], active: set[str], guard: ast.expr | None) -> None:
        for stmt in stmts:
            if _is_gate_exit(stmt):
                names = _guard_names(guard) if guard is not None else set()
                for mode in active:
                    found[mode].append(names)
                continue
            if isinstance(stmt, ast.If):
                resolved = _mode_test(stmt.test)
                if resolved is not None:
                    kind, modes = resolved
                    if kind == "not":
                        body_active, else_active = active - modes, active & modes
                    else:
                        body_active, else_active = active & modes, active - modes
                    walk(stmt.body, body_active, guard)
                    walk(stmt.orelse, else_active, guard)
                    if _terminates(stmt.body):
                        active = else_active
                    elif stmt.orelse and _terminates(stmt.orelse):
                        active = body_active
                    continue
                walk(stmt.body, active, stmt.test)
                walk(stmt.orelse, active, None)
                continue
            if isinstance(stmt, (ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith, ast.Try)):
                for field in ("body", "orelse", "finalbody"):
                    sub = getattr(stmt, field, None)
                    if isinstance(sub, list):
                        walk(sub, active, guard)
                for handler in getattr(stmt, "handlers", []):
                    walk(handler.body, active, guard)
                continue
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Reached where it is CALLED; recording it here would attribute
                # a closure's exit to whatever mode region defined it.
                continue

    walk(func.body, set(_MODES), None)
    return found


def find_blind_channels(source: str, label: str) -> list[dict[str, object]]:
    """Return one record per (function, degradation token) asymmetry."""
    findings: list[dict[str, object]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        by_mode = _gate_guards_by_mode(node)
        gated = {mode: guards for mode, guards in by_mode.items() if guards}
        if len(gated) < 2:
            continue
        for token in _DEGRADATION_TOKENS:
            carriers = {mode for mode, guards in gated.items() if any(any(token in name for name in g) for g in guards)}
            if not carriers:
                continue
            blind = set(gated) - carriers
            if blind:
                findings.append(
                    {
                        "module": label,
                        "function": node.name,
                        "token": token,
                        "gates_on_it": sorted(carriers),
                        "blind_to_it": sorted(blind),
                    }
                )
    return findings


# ---------------------------------------------------------------------------
# Positive control -- a scanner that cannot fail is not a scanner
# ---------------------------------------------------------------------------

_POSITIVE_CONTROL = """
def command(ctx, fail_on_found):
    json_mode = ctx.obj.get("json")
    if json_mode:
        click.echo(to_json(envelope))
        if fail_on_found and (violations or scan_incomplete):
            raise GateFailureError(verdict)
        return
    click.echo(f"VERDICT: {verdict}")
    if fail_on_found and violations:
        raise GateFailureError(verdict)
"""


def test_positive_control_is_detected() -> None:
    """The exact pre-fix `ignore-drift` shape must be reported.

    Without this, a scanner that silently stopped parsing would report the
    whole surface clean and read as the strongest possible result.
    """
    findings = find_blind_channels(_POSITIVE_CONTROL, "<control>")
    tokens = {f["token"] for f in findings}
    assert "scan_incomplete" in tokens, f"positive control not detected; got {findings}"
    hit = next(f for f in findings if f["token"] == "scan_incomplete")
    assert hit["gates_on_it"] == ["json"]
    assert "text" in hit["blind_to_it"]


def test_corrected_shape_is_not_reported() -> None:
    """The fix must actually clear the finding, not merely move it.

    The corrected text branch nests its gate inside an
    ``if scan_incomplete:`` disclosure block. A scanner charging the whole
    enclosing guard stack to the gate reports that as the SAME asymmetry with
    the channels swapped -- which is how a lint ends up with an exemption list
    instead of a clean result.
    """
    corrected = """
def command(ctx, fail_on_found):
    json_mode = ctx.obj.get("json")
    gate_failed = gate_should_fail(fail_on_found, findings=violations, scan_incomplete=scan_incomplete)
    if json_mode:
        click.echo(to_json(envelope))
        if gate_failed:
            raise GateFailureError(verdict)
        return
    click.echo(f"VERDICT: {verdict}")
    if scan_incomplete:
        click.echo("  Nothing was measured, so nothing is proven clean.")
        if gate_failed:
            raise GateFailureError(verdict)
        return
    if gate_failed:
        raise GateFailureError(verdict)
"""
    assert find_blind_channels(corrected, "<corrected>") == []


# ---------------------------------------------------------------------------
# The surface scan
# ---------------------------------------------------------------------------


def _command_module_paths() -> list[tuple[str, Path]]:
    seen: dict[str, Path] = {}
    for _name, (module_path, _attr) in sorted(_COMMANDS.items()):
        module = importlib.import_module(module_path)
        source = getattr(module, "__file__", None)
        if source:
            seen.setdefault(module_path, Path(source))
    return sorted(seen.items())


def test_no_registered_command_gates_on_a_degradation_in_only_some_channels() -> None:
    """Every registered command module, every output mode. Zero exemptions.

    Measured 2026-08-08: 274 modules, 0 findings after the ignore-drift fix,
    and the same scan run against the pre-fix file at d8cd2fb4 reports
    ``ignore_drift / scan_incomplete / gates_on=[json] / blind=[text, sarif]``.
    Because the result is genuinely zero, this rule ships with no allowlist --
    a new violation cannot be absorbed by an exemption nobody re-reads.
    """
    modules = _command_module_paths()
    assert len(modules) > 200, f"only {len(modules)} command modules resolved -- the enumeration broke"

    findings: list[dict[str, object]] = []
    unparsed: list[str] = []
    for module_path, file_path in modules:
        try:
            source = file_path.read_text(encoding="utf-8")
        except OSError as exc:  # pragma: no cover - a module we cannot read is UNKNOWN
            unparsed.append(f"{module_path}: unreadable ({exc})")
            continue
        try:
            findings += find_blind_channels(source, module_path)
        except SyntaxError as exc:  # pragma: no cover
            unparsed.append(f"{module_path}: {exc}")

    assert not unparsed, (
        "these command modules could not be analysed, so their result is UNKNOWN, "
        "not clean:\n  " + "\n  ".join(unparsed)
    )

    if not findings:
        return

    detail = "\n".join(
        f"  {f['module']}.{f['function']}: gate exits in {f['gates_on_it']} are guarded by "
        f"{f['token']!r}, but the gate exits in {f['blind_to_it']} are not"
        for f in findings
    )
    pytest.fail(
        "a command refuses on a degradation in one output channel and ignores it in another:\n"
        f"{detail}\n\n"
        "Compute the gate decision ONCE, before the first `if json_mode:` branch "
        "(see roam.exit_codes.gate_should_fail), and have every channel read that "
        "one boolean. Two guards that must agree are two guards that will not."
    )
