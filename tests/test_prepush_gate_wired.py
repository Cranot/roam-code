"""Drift guard: the pre-push structural-gate bundle stays wired.

``scripts/prepush_check.py`` runs the repo-wide structural drift-guards
locally before ``git push`` — the gate that would have prevented this
session's ~14 CI fix-forward cascade (design:
``(internal memo)``). The value of that gate is
entirely in the SET of guards it bundles. If a guard test is renamed or
deleted and silently drops out of the FAST tuple, the pre-push gate quietly
stops catching that drift class — exactly the silent-rot failure mode the
"ship a structural guard with the campaign" rule warns against.

This module pins the contract via pure AST inspection (no subprocess, no
index build) so it runs in the FAST bundle itself:

1. ``scripts/prepush_check.py`` exists and parses as valid Python.
2. It exposes the ``FAST_PYTEST_GUARDS`` and ``FULL_PYTEST_GUARDS`` tuples,
   a ``main`` callable, and a ``repo_root`` resolver.
3. Every guard named in either tuple resolves to a real ``tests/`` file —
   so a renamed/deleted guard fails this test instead of silently leaving
   the bundle.
4. The expected high-frequency FAST guards (the dominant fix-forward class)
   are all present.
"""

from __future__ import annotations

import ast

import pytest

from tests._helpers.repo_root import repo_root

REPO_ROOT = repo_root()
SCRIPT_PATH = REPO_ROOT / "scripts" / "prepush_check.py"
TESTS_DIR = REPO_ROOT / "tests"

# The FAST guards the design memo's back-test proved catch the dominant
# structural-drift fix-forward class. The script may carry MORE than these,
# but never fewer — dropping one of these is the regression this guards.
_EXPECTED_FAST_GUARDS = frozenset(
    {
        "test_w547_severity_drift.py",
        "test_law4_lint.py",
        "test_law4_anchor_counts.py",
        "test_w588_fragile_path_drift.py",
        "test_w662_bare_except_drift.py",
        "test_optional_imports_guarded.py",
        "test_findings_detector_count_drift.py",
        "test_detector_registry.py",
        "test_w444_mcp_tool_names_no_dedupe.py",
        "test_w462_landing_page_tool_count_drift.py",
        "test_mcp_server_card_hash.py",
        "test_compound_recipe_registry.py",
        # 2026-07-28: the closed-set registry inventories. Four commits passed
        # the pre-push hook and then took all four CI lanes red because files
        # were added without being registered in these. They are pure
        # file/AST assertions with no index dependency and measured +0.26s on
        # the bundle, so there is no performance argument for dropping them.
        "test_public_allowlist.py",
        "test_workflow_dependency_lock_policy.py",
        "test_publish_provenance_workflow.py",
        "test_composite_action_security.py",
        # 2026-08-07: this file, pinning its own membership. Its docstring
        # claimed it "runs in the FAST bundle itself"; measured, a collect-only
        # over FAST_PYTEST_GUARDS returned 0 node ids from it, so every
        # contract it pins was enforced only by CI, after the push. A guard on
        # the pre-push script that the pre-push script does not run is a claim
        # of local coverage that does not exist.
        "test_prepush_gate_wired.py",
    }
)

HOOK_PATH = REPO_ROOT / ".githooks" / "pre-push"


def _strip_shell_comments(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _parse_script() -> ast.Module:
    assert SCRIPT_PATH.exists(), (
        f"Expected the pre-push gate at {SCRIPT_PATH}. This script runs the "
        "structural drift-guards locally before push; without it the ~14 "
        "fix-forward cascade class is uncaught. See "
        "(internal memo)."
    )
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    return ast.parse(source, filename=str(SCRIPT_PATH))


def _extract_tuple_strings(tree: ast.Module, name: str) -> list[str]:
    """Return the string literals assigned to module-level ``name`` (a tuple)."""
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        targets = [t for t in node.targets if isinstance(t, ast.Name) and t.id == name]
        if not targets:
            # Also accept annotated assignment (name: type = (...)).
            continue
        if isinstance(node.value, (ast.Tuple, ast.List)):
            return [
                elt.value for elt in node.value.elts if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            ]
    # AnnAssign path (FAST_PYTEST_GUARDS: tuple[str, ...] = (...)).
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            if isinstance(node.value, (ast.Tuple, ast.List)):
                return [
                    elt.value for elt in node.value.elts if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                ]
    raise AssertionError(f"Could not find a module-level tuple/list literal named {name!r} in {SCRIPT_PATH.name}.")


def test_prepush_script_exists_and_parses() -> None:
    """The script exists and is valid, parseable Python."""
    tree = _parse_script()
    assert isinstance(tree, ast.Module)


def test_prepush_script_exposes_expected_symbols() -> None:
    """The gate exposes the contract symbols other surfaces depend on."""
    tree = _parse_script()
    top_level_names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            top_level_names.add(node.name)
        elif isinstance(node, ast.Assign):
            top_level_names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            top_level_names.add(node.target.id)

    for required in ("FAST_PYTEST_GUARDS", "FULL_PYTEST_GUARDS", "main", "repo_root"):
        assert required in top_level_names, (
            f"scripts/prepush_check.py must define {required!r}; the .githooks/pre-push "
            f"shim and this drift guard depend on it. Found: {sorted(top_level_names)}"
        )


def test_release_tier_registered() -> None:
    """The pre-tag preflight tier (--release) must stay wired.

    CONTRIBUTING.md + .githooks/pre-push document `prepush_check.py --release`
    as the gate that runs what CI runs before a tag. If the flag is renamed or
    dropped, that documented preflight silently no-ops — the exact gap that let
    the 13.8.0 tag take 8 sequential CI rounds. Import-light source assertion
    (mirrors the other guards in this file — no module import needed).
    """
    src = SCRIPT_PATH.read_text(encoding="utf-8")
    for flag in ("--release", "--full", "--fast"):
        assert flag in src, (
            f"scripts/prepush_check.py must register the {flag!r} tier; "
            ".githooks/pre-push + CONTRIBUTING.md document it as a release gate."
        )


def test_fast_bundle_contains_expected_guards() -> None:
    """Every high-frequency FAST guard from the design back-test is bundled."""
    tree = _parse_script()
    fast = set(_extract_tuple_strings(tree, "FAST_PYTEST_GUARDS"))
    missing = _EXPECTED_FAST_GUARDS - fast
    assert not missing, (
        f"FAST_PYTEST_GUARDS dropped expected guard(s): {sorted(missing)}. "
        "These are the dominant structural-drift fix-forward class (severity-rank, "
        "LAW-4, fragile-path, card-hash, detector-count, compound-recipe). Re-add "
        "them or update (internal memo) if intentionally removed."
    )


def _driver_whole_tree_leak_invocations(tree: ast.Module) -> int:
    """Count subprocess argv literals that run the scanner in ``--all`` mode."""
    found = 0
    for node in ast.walk(tree):
        if not isinstance(node, (ast.List, ast.Tuple)):
            continue
        strings = {elt.value for elt in node.elts if isinstance(elt, ast.Constant) and isinstance(elt.value, str)}
        if "--all" in strings and any(s.endswith("scan_internal_language.py") for s in strings):
            found += 1
    return found


def test_whole_tree_leak_scan_runs_exactly_once_in_the_push_path() -> None:
    """The whole-tree anti-leak scan must run — and must run only once.

    Both halves of this matter and they pull in opposite directions:

    * It must RUN. The hook's other two leak gates scope themselves to the
      pushed ref updates, which by construction cannot see a leak sitting in
      a file this push does not touch — the case a GROWN pattern catalogue
      creates. Whole-tree is the only scope that catches that, so this gate
      may never be rescoped to a range to save time. The 2026-05-20 incident
      (a customer name reaching the public repo, history purged by
      force-push) is what it exists to prevent.
    * It must run ONCE. Until 2026-07-28 ``.githooks/pre-push`` ran a
      byte-identical ``--all`` invocation of its own moments before handing
      off to ``prepush_check.py``, over the same unchanged tree. Measured:
      36.6s, roughly a third of the whole hook, for zero extra coverage.

    Pinning both ends means neither a well-meaning "add a backstop" nor a
    well-meaning "make it faster" can quietly restore the waste or remove
    the protection.
    """
    driver_count = _driver_whole_tree_leak_invocations(_parse_script())
    assert driver_count == 1, (
        f"scripts/prepush_check.py should run the whole-tree "
        f"`scan_internal_language.py --all` gate exactly once; found {driver_count}. "
        "It is the single whole-tree pass in the push path (see _run_leak_gate)."
    )

    hook_src = _strip_shell_comments(HOOK_PATH.read_text(encoding="utf-8"))
    offenders = [
        line.strip() for line in hook_src.splitlines() if "scan_internal_language.py" in line and "--all" in line
    ]
    assert not offenders, (
        "`.githooks/pre-push` must NOT run `scan_internal_language.py --all` itself. "
        f"Found: {offenders}. That invocation is byte-identical to the one "
        "scripts/prepush_check.py already runs seconds later over the same tree, "
        "and cost a measured 36.6s per push for no additional coverage. The hook's "
        "own leak gates are the sub-second --pre-push-updates ones."
    )


def test_hook_still_delegates_the_whole_tree_scan() -> None:
    """Removing the duplicate must not have removed the protection.

    The dedupe above is only safe because the hook still runs the driver,
    which still runs the whole-tree scan. If the hook ever stops delegating,
    the push path loses the whole-tree leak gate entirely — a silent
    downgrade that would look like a speed-up.
    """
    hook_src = _strip_shell_comments(HOOK_PATH.read_text(encoding="utf-8"))
    assert "scripts/prepush_check.py" in hook_src, (
        ".githooks/pre-push must still invoke scripts/prepush_check.py: that is now "
        "the ONLY thing running the whole-tree anti-leak scan on the push path."
    )


def _release_note_bullets(tree: ast.Module) -> list[str]:
    """Return the bullet lines of the RELEASE 'NOT run by any tier' note.

    The note is written as adjacent string literals inside one ``print(...)``,
    which Python concatenates at parse time into a single ``ast.Constant`` — so
    locating it is a search for that one constant, not a reconstruction.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and "NOT run by any tier" in node.value:
            bullets = []
            for line in node.value.splitlines():
                stripped = line.strip().removeprefix("[prepush]").strip()
                if stripped.startswith("- "):
                    bullets.append(stripped[2:].strip())
            return bullets
    raise AssertionError(
        "Could not find the RELEASE uncovered-lanes note in prepush_check.py "
        "(_print_summary). It must contain the phrase 'NOT run by any tier'."
    )


_TIER_FLAG_NAMES = frozenset({"full", "release"})


def _is_tier_conditional(stmt: ast.stmt) -> bool:
    """True for a statement whose execution depends on the ``--full``/``--release`` tier.

    ``main()`` derives exactly two tier flags (``release = args.release`` and
    ``full = args.full or release``) and branches on them by name, so a
    conservative name test over the branch condition is a complete reading of
    tier-conditionality in this script. Attribute form (``args.release``) is
    accepted too, so hoisting the branch back onto the namespace does not
    silently reclassify a release-only gate as always-on.

    Deliberately conservative: a compound test that mentions a tier flag at all
    (``if release and not runner.<gate>().passed:``) counts as tier-conditional.
    Under-collecting keeps the note guard from demanding that a genuinely
    unproven lane stop being disclaimed.

    Residual, stated rather than implied: a branch that is conditional on
    something OTHER than a tier — ``if os.environ.get(...):`` at the top level
    of ``main()`` — is NOT recognised here, so a gate inside it reads as
    always-on. No such branch exists in ``main()`` today (the only top-level
    conditions are the two tier flags). If one is added, extend this predicate
    rather than re-narrowing the extractor by statement kind — that is the
    filter that produced the blind spot in the first place.
    """
    if not isinstance(stmt, ast.If):
        return False
    for node in ast.walk(stmt.test):
        if isinstance(node, ast.Name) and node.id in _TIER_FLAG_NAMES:
            return True
        if isinstance(node, ast.Attribute) and node.attr in _TIER_FLAG_NAMES:
            return True
    return False


def _unconditional_gate_labels(tree: ast.Module) -> dict[str, str]:
    """Map every gate label that runs in EVERY tier to the method that runs it.

    The question this answers is "does this gate run in every tier", and the
    only thing that decides that is whether the call sits under an ``if full:``
    / ``if release:`` branch. So the filter is tier-conditionality alone: skip
    the tier branches among ``main``'s direct-child statements, and read
    ``runner.<…>`` calls ANYWHERE inside what survives.

    That is deliberately not a filter on statement KIND. Two earlier versions
    filtered by shape and each was blind to the shapes it had not enumerated:

    1. Reading only ``runner.<method>()`` grouping helpers (``_run_leak_gate``,
       ``_run_ruff``, …) missed a direct ``runner._run("<label>", …)``: the
       method resolves to ``GateRunner._run``, which contains no nested
       ``self._run(...)``, so the gate contributed NOTHING.
    2. Reading both of those but only as ``ast.Expr`` statements missed every
       VALUE position — and the value position is house style here, since
       ``main()`` already writes
       ``if release and not runner.run_release_temp_capacity_gate().passed:``.

    Measured 2026-08-07 against the real ``scripts/prepush_check.py`` with one
    unconditional ``roam compatibility --ci`` gate (a label the RELEASE note
    disclaims) spliced in three ways — ``if not runner._run(…).passed:``,
    ``if not runner._run_compat_gate().passed:``, and
    ``compat = runner._run(…)``. All three returned the SAME 7 labels as the
    unmutated tree, so the note guard reported ``liars=0`` and passed while the
    note disclaimed a gate every tier now ran. A gate reporting coverage over a
    shape it cannot read is this guard's own defect class aimed at itself.

    Two shapes then turn a collected call into labels:

    * **Indirect** — ``runner.<method>(…)`` where ``<method>`` is a grouping
      helper that registers labels via ``self._run("<label>", …)`` in its body.
    * **Direct** — ``runner._run("<label>", …)``, the label being the literal
      first argument.
    """
    main_fn = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "main"),
        None,
    )
    assert main_fn is not None, "scripts/prepush_check.py must define main()."

    unconditional_calls = [
        node
        for stmt in main_fn.body
        if not _is_tier_conditional(stmt)
        for node in ast.walk(stmt)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "runner"
    ]
    assert unconditional_calls, (
        "Found no unconditional `runner.<gate>()` calls in main(); the AST shape "
        "this guard reads has changed and the guard is now blind."
    )

    labels: dict[str, str] = {}

    # Shape 1: grouping helper — walk the method body for self._run("<label>").
    for method in [call.func.attr for call in unconditional_calls]:
        fn = next(
            (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == method),
            None,
        )
        if fn is None:
            continue
        for call in ast.walk(fn):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "_run"
                and call.args
                and isinstance(call.args[0], ast.Constant)
                and isinstance(call.args[0].value, str)
            ):
                labels.setdefault(call.args[0].value, method)

    # Shape 2: the label is the literal first argument of the call in main().
    for call in unconditional_calls:
        if call.func.attr == "_run" and call.args and isinstance(call.args[0], ast.Constant):
            if isinstance(call.args[0].value, str):
                labels.setdefault(call.args[0].value, "main")

    # Residual tripwire for TOTAL blindness, and only that. `unconditional_calls`
    # being non-empty does NOT imply the extractor read anything: on a main()
    # whose always-on gates were all direct-form, the first extractor saw
    # methods == ['_run'] — non-empty, so the assertion above passed — and still
    # returned zero labels, leaving the guard to compare an empty set against
    # the note and report green. A guard that can only pass is the defect it
    # exists to prevent, so an extractor that reads NOTHING must fail loudly
    # rather than certify silence.
    #
    # What it does NOT measure: per-gate coverage. It is a floor, not a census,
    # so a PARTIALLY blind extractor stays silent — the value-position blindness
    # above returned 7 labels and tripped nothing. A shape no branch can read is
    # already in the tree: `run_release_temp_capacity_gate` builds its GateResult
    # inline and appends to self.results without ever calling `self._run` with a
    # literal label, so neither branch can name it (it is tier-conditional today,
    # so it is correctly excluded anyway — but nothing here would notice if it
    # were not). Closing that class needs a per-gate check against
    # `runner.results`, not a stronger tripwire.
    assert labels, (
        "Read no gate labels from main()'s unconditional calls "
        f"({[call.func.attr for call in unconditional_calls]}); the wiring shape has "
        "changed again and this guard is blind. Teach _unconditional_gate_labels "
        "the new shape — do NOT delete the assertion."
    )
    return labels


def test_release_note_never_disclaims_a_gate_it_actually_runs() -> None:
    """The tier's own coverage note must not name a gate that runs in every tier.

    The RELEASE summary prints a list of CI lanes that "are NOT run by any tier
    and stay unproven here". That note exists to stop an operator over-reading
    green — so a FALSE entry in it is worse than no note at all: it tells the
    reader a gate is unproven when the push path just proved it, and invites
    someone to spend a CI round re-proving it.

    Measured 2026-08-07: the note listed `roam ignore-drift --fail-on-found`,
    which `_run_leak_gate` registers and `main()` runs unconditionally, before
    any tier branching — a live FAST run printed
    `[prepush] roam ignore-drift --fail-on-found: PASS (0.4s)`.

    This guard makes the CLASS un-reintroducible: it derives BOTH sides from
    the source (the gate labels that actually run in every tier, and the
    bullets of the note) instead of pinning either literal, so adding a new
    unconditional gate whose name is also disclaimed fails here rather than in
    a reader's head.
    """
    tree = _parse_script()
    bullets = _release_note_bullets(tree)
    assert bullets, "The RELEASE uncovered-lanes note lists no lanes; its bullet shape has changed."

    labels = _unconditional_gate_labels(tree)
    liars = [(label, method, bullet) for label, method in labels.items() for bullet in bullets if label in bullet]
    assert not liars, (
        "scripts/prepush_check.py's RELEASE note claims a gate is NOT run by any "
        "tier, but main() runs it unconditionally in every tier:\n"
        + "\n".join(
            f"  - {label!r} is registered by {method}() but disclaimed in the bullet {bullet!r}"
            for label, method, bullet in liars
        )
        + "\nFix the NOTE to match reality — narrow or drop the bullet. Do NOT "
        "delete the gate to make the sentence true."
    )


_EXTRACTOR_FIXTURE = """
class GateRunner:
    def _run(self, name, argv, fix_hint):
        ...

    def _run_group(self):
        self._run("grouped gate", [], fix_hint="")

    def run_bundle(self, guards, tier):
        ...


def main(argv=None):
    runner = GateRunner()
    runner._run_group()
    runner._run("direct gate", [], fix_hint="")
    runner.run_bundle(FAST, "FAST")
    if release:
        runner._run("release-only gate", [], fix_hint="")
    return 0
"""


def test_gate_label_extractor_reads_both_wiring_shapes() -> None:
    """Both shapes ``main()`` uses to wire an always-on gate must be read.

    ``_unconditional_gate_labels`` is the measuring half of the note guard
    above, and a note guard that cannot see a gate certifies the note over a
    surface it never read — the same defect the note guard exists to prevent,
    aimed at itself.

    Measured 2026-08-07 on a copy of ``prepush_check.py`` carrying one extra
    unconditional ``runner._run("roam compatibility --ci", …)`` — a label the
    RELEASE note already disclaims, and a plausible next move since wiring that
    lane into a tier is exactly what the bullet anticipates. The pre-widening
    extractor returned the same 7 labels as the real tree and the note guard
    passed: ``method='_run'`` resolved to ``GateRunner._run``, which contains no
    nested ``self._run(...)``, so the gate contributed nothing.

    Three properties, one fixture:

    * the grouping shape (``runner._run_group()`` → ``self._run("…")``) is read;
    * the direct shape (``runner._run("…", …)`` in ``main()``) is read;
    * a direct call nested under ``if release:`` is NOT read — it is genuinely
      tier-conditional, and collecting it would force the note to stop
      disclaiming lanes that really do not run in FAST.
    """
    labels = _unconditional_gate_labels(ast.parse(_EXTRACTOR_FIXTURE))

    assert "grouped gate" in labels, f"lost the grouping shape `runner.<method>()` → `self._run(...)`. Read: {labels}"
    assert "direct gate" in labels, (
        '`runner._run("<label>", ...)` written directly in main() contributes no '
        f"label, so a gate wired that way can be disclaimed in the RELEASE "
        f"'NOT run by any tier' note while running in every tier. Read: {labels}"
    )
    assert "release-only gate" not in labels, (
        "a `runner._run(...)` nested under `if release:` is tier-conditional and "
        f"must stay uncollected; collecting it would make the note guard demand "
        f"that genuinely unproven lanes stop being disclaimed. Read: {labels}"
    )


_VALUE_POSITION_FIXTURE = """
class GateRunner:
    def _run(self, name, argv, fix_hint):
        ...

    def _run_compat_gate(self):
        return self._run("helper-in-value gate", [], fix_hint="")

    def run_bundle(self, guards, tier):
        ...


def main(argv=None):
    runner = GateRunner()
    if not runner._run("if-value gate", [], fix_hint="").passed:
        return 1
    if not runner._run_compat_gate().passed:
        return 1
    assigned = runner._run("assigned gate", [], fix_hint="")
    runner.run_bundle(FAST, "FAST")
    if release and not runner._run("release-guarded gate", [], fix_hint="").passed:
        return 1
    if full:
        runner._run("full-only gate", [], fix_hint="")
    return 0 if assigned.passed else 1
"""


def test_gate_label_extractor_reads_value_position_gates() -> None:
    """A gate whose call sits in a VALUE position still runs in every tier.

    The statement-position shapes (``runner._run_group()`` /
    ``runner._run("…")`` as a bare expression) are not the only way main()
    wires an always-on gate, and they are not even the shape main() reaches
    for when a gate's RESULT decides control flow. main() already writes
    ``if release and not runner.run_release_temp_capacity_gate().passed:`` —
    so the value position is established house style, and the next
    unconditional gate is at least as likely to be written that way.

    Measured 2026-08-07 against the real ``scripts/prepush_check.py`` with one
    unconditional ``roam compatibility --ci`` gate spliced in after
    ``runner._run_leak_gate()`` — a label the RELEASE note disclaims. All three
    value-position shapes returned the SAME 7 labels as the unmutated tree, so
    the note guard reported ``liars=0`` and passed on a tree whose note lies:

    * ``if not runner._run("…", …).passed:``      (test position)
    * ``if not runner._run_compat_gate().passed:``(test position, via helper)
    * ``compat = runner._run("…", …)``            (assignment value)

    The cause was the ``isinstance(stmt, ast.Expr)`` filter: a call that is not
    the whole statement is not an ``ast.Expr``, so it was never a candidate.
    Filtering by STATEMENT KIND asks "how is this written", when the only
    question that matters is "does this run in every tier" — which is answered
    by tier-conditionality alone. Hence the extractor now skips exactly the
    ``if full:`` / ``if release:`` branches and reads runner calls anywhere in
    what survives.
    """
    labels = _unconditional_gate_labels(ast.parse(_VALUE_POSITION_FIXTURE))

    for shape, label in (
        ("`if not runner._run(...).passed:` (test position)", "if-value gate"),
        ("`if not runner._run_compat_gate().passed:` (test position via helper)", "helper-in-value gate"),
        ("`assigned = runner._run(...)` (assignment value)", "assigned gate"),
    ):
        assert label in labels, (
            f"{shape} contributes no label, so a gate wired that way runs in "
            f"every tier while the RELEASE note may keep disclaiming it. Read: {sorted(labels)}"
        )

    assert "release-guarded gate" not in labels, (
        "a runner call inside `if release and ...:` is tier-conditional and must "
        f"stay uncollected — widening must not start demanding that genuinely "
        f"unproven lanes stop being disclaimed. Read: {sorted(labels)}"
    )
    assert "full-only gate" not in labels, (
        f"a runner call under `if full:` is tier-conditional and must stay uncollected. Read: {sorted(labels)}"
    )


def test_note_guard_catches_a_value_position_gate_in_the_real_script() -> None:
    """End-to-end: splice a disclaimed gate into the REAL script, note guard must fail.

    ``test_gate_label_extractor_reads_value_position_gates`` pins the extractor
    on a synthetic fixture; this pins the property that actually matters — that
    the NOTE guard goes red — against the real ``main()``, its real tier
    branching and the real RELEASE bullets. A fixture can drift away from the
    script it stands in for; this cannot.

    The mutation is the plausible next commit, not a contrived one: wiring
    ``roam compatibility --ci`` into the push path is exactly what the bullet
    "roam compatibility --ci / --require-coverage" anticipates someone doing,
    and forgetting to delete the bullet afterwards is the whole failure class.
    """
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    anchor = "    runner._run_leak_gate()\n"
    assert anchor in source, (
        f"Expected the unconditional `{anchor.strip()}` call in main() as the splice "
        "point for this mutation. main()'s wiring moved — re-anchor this test rather "
        "than deleting it; it is the only end-to-end check that the note guard can fail."
    )
    disclaimed = "roam compatibility --ci"
    assert any(disclaimed in bullet for bullet in _release_note_bullets(ast.parse(source))), (
        f"This test mutates in a gate labelled {disclaimed!r} because the RELEASE note "
        "disclaims it. The note no longer does, so the mutation would prove nothing — "
        "pick another disclaimed label."
    )

    for shape, injected in (
        ("test position", f'    if not runner._run("{disclaimed}", [], fix_hint="").passed:\n        return 1\n'),
        ("assignment value", f'    compat = runner._run("{disclaimed}", [], fix_hint="")\n    del compat\n'),
    ):
        mutated = ast.parse(source.replace(anchor, anchor + injected))
        labels = _unconditional_gate_labels(mutated)
        bullets = _release_note_bullets(mutated)
        liars = [(label, method, b) for label, method in labels.items() for b in bullets if label in b]
        assert liars, (
            f"An unconditional `{disclaimed}` gate wired into main() in the {shape} "
            "was not detected: the RELEASE note still disclaims a gate every tier now "
            f"runs, and test_release_note_never_disclaims_a_gate_it_actually_runs would "
            f"pass on that tree. Labels read: {sorted(labels)}"
        )


def test_gate_label_extractor_refuses_to_certify_silence() -> None:
    """An extractor that reads NOTHING AT ALL must fail, not report an empty set.

    The note guard's verdict is ``labels ∩ bullets == ∅``. With zero labels that
    is vacuously true, so a totally blind extractor reports green with maximum
    confidence — an absent measurement published as a benign result.

    The older `unconditional_methods` tripwire does not cover this: it fires
    only when main() has no ``runner.<…>()`` calls at all. Measured on a main()
    whose always-on gates were all direct-form, the first extractor saw
    ``methods == ['_run']`` — non-empty, tripwire silent — and still returned
    zero labels. Asserting on the OUTPUT catches that.

    SCOPE — this is a floor, not a census, and the difference is the whole
    point of stating it. It fires only on TOTAL blindness. A PARTIALLY blind
    extractor passes it: measured 2026-08-07, the value-position blindness
    returned 7 labels from the real script while contributing NOTHING for an
    unconditional gate spliced into main(), and this assertion stayed silent
    throughout. A shape neither branch can read is in the tree right now —
    ``run_release_temp_capacity_gate`` constructs its ``GateResult`` inline and
    appends to ``self.results`` without ever calling ``self._run`` with a
    literal label. Per-shape coverage is pinned by the
    ``test_gate_label_extractor_reads_*`` tests above, each naming the shapes it
    proves; do not read this one as covering shapes they do not enumerate.
    """
    blind_source = (
        "def main(argv=None):\n    runner = GateRunner()\n    runner.run_bundle(FAST, 'FAST')\n    return 0\n"
    )

    with pytest.raises(AssertionError, match="Read no gate labels"):
        _unconditional_gate_labels(ast.parse(blind_source))

    # And it stays quiet on a tree that does yield labels.
    assert _unconditional_gate_labels(ast.parse(_EXTRACTOR_FIXTURE))


def test_every_bundled_guard_file_exists() -> None:
    """No bundled guard may reference a renamed/deleted test file."""
    tree = _parse_script()
    bundled = set(_extract_tuple_strings(tree, "FAST_PYTEST_GUARDS"))
    bundled |= set(_extract_tuple_strings(tree, "FULL_PYTEST_GUARDS"))
    missing = sorted(name for name in bundled if not (TESTS_DIR / name).exists())
    assert not missing, (
        f"prepush_check.py bundles test file(s) that do not exist: {missing}. "
        "A renamed or deleted guard silently drops out of the pre-push bundle. "
        "Update FAST_PYTEST_GUARDS / FULL_PYTEST_GUARDS to the new name."
    )
