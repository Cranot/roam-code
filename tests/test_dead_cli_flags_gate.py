"""Drift guard for the dead-CLI-flag gate.

A ``@click.option`` the command body never reads is a flag the user passes and
we silently discard — the 2026-06-07 ``roam deps --full`` bug. An auditor for
that class already existed (``scripts/audit_dead_cli_flags.py``) but was
advisory by its own docstring and had ZERO callers in ``.github/workflows/``,
``scripts/prepush_check.py`` or ``.githooks/``. It read as coverage and caught
nothing: by 2026-08-06 eight flags had accumulated behind it.

So this file pins BOTH halves:

1. the gate's verdict on the live tree (``--fail-on-found`` exits 0), and
2. the wiring — that a CI job actually runs it with that flag, and that this
   very guard is in the pre-push FAST tier.

(2) is the load-bearing one. Without it, deleting the CI step returns the
auditor to exactly the decorative state that let the eight accumulate, and
nothing would notice.
"""

from __future__ import annotations

import subprocess
import sys

import yaml

from tests._helpers.repo_root import repo_root

ROOT = repo_root()
AUDITOR = ROOT / "scripts" / "audit_dead_cli_flags.py"
ALLOWLIST = ROOT / "scripts" / "dead_cli_flags_allowlist.txt"
WORKFLOW_DIR = ROOT / ".github" / "workflows"

GATE_INVOCATION = "scripts/audit_dead_cli_flags.py --fail-on-found"


def test_no_dead_cli_flags_on_this_tree():
    """Every click param is either read by its command body or allowlisted."""
    proc = subprocess.run(
        [sys.executable, str(AUDITOR), "--fail-on-found"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert proc.returncode == 0, (
        "dead CLI flag gate failed — a @click.option is declared but never read by its "
        "command body, so the flag is silently discarded at runtime.\n\n" + proc.stdout + proc.stderr
    )


def test_gate_is_wired_into_ci():
    """Some CI job must run the auditor WITH --fail-on-found.

    The flag is the whole point: without it the script prints its findings and
    exits 0, so a CI step that omitted it would be green on a tree full of dead
    flags — the failure mode this gate replaced.
    """
    hits = []
    for wf in sorted(WORKFLOW_DIR.glob("*.yml")):
        text = wf.read_text(encoding="utf-8")
        if GATE_INVOCATION in text:
            hits.append(wf.name)
    assert hits, (
        f"no workflow in {WORKFLOW_DIR} runs `{GATE_INVOCATION}`. "
        "An auditor with no caller is not a guard — it reads as covered while catching nothing."
    )

    # The step must live in a job that actually exists and is not disabled.
    for name in hits:
        doc = yaml.safe_load((WORKFLOW_DIR / name).read_text(encoding="utf-8"))
        runs = [
            step.get("run", "")
            for job in (doc.get("jobs") or {}).values()
            for step in (job.get("steps") or [])
            if isinstance(step, dict)
        ]
        assert any(GATE_INVOCATION in r for r in runs), (
            f"{name} mentions `{GATE_INVOCATION}` but not inside any job step — "
            "a commented-out or stringly-embedded invocation does not run."
        )


def test_gate_is_wired_into_the_prepush_fast_tier():
    """This guard must be in the pre-push FAST bundle, not only in CI.

    CI-only means the feedback arrives after the push, which is the loop the
    prepush tier exists to shorten. Read off the source tuple rather than
    importing the script, so a broken import can't read as "not wired".
    """
    import ast

    src = (ROOT / "scripts" / "prepush_check.py").read_text(encoding="utf-8")
    guards: list[str] = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.AnnAssign | ast.Assign):
            continue
        targets = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
        names = {t.id for t in targets if isinstance(t, ast.Name)}
        if "FAST_PYTEST_GUARDS" not in names or node.value is None:
            continue
        guards = [e.value for e in ast.walk(node.value) if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    assert guards, "FAST_PYTEST_GUARDS not found in scripts/prepush_check.py"
    assert "test_dead_cli_flags_gate.py" in guards, (
        f"the dead-CLI-flag guard dropped out of the pre-push FAST tier — FAST_PYTEST_GUARDS currently holds {guards}"
    )


def test_allowlist_entries_carry_a_reason():
    """An exemption without a written reason is an exemption nobody can review.

    The allowlist exists for two narrow cases (AST heuristic false positives,
    and deliberate accepted no-ops). Both are judgement calls, so both have to
    say WHY on the line, or the file rots into a second blind spot.
    """
    assert ALLOWLIST.exists(), f"{ALLOWLIST} is missing — the gate falls back to an EMPTY allowlist"
    bad = []
    for raw in ALLOWLIST.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        entry, sep, reason = raw.partition("#")
        key = entry.strip()
        assert key.count("::") == 2, f"malformed allowlist entry (want file::function::param): {raw!r}"
        if not sep or len(reason.strip()) < 20:
            bad.append(key)
    assert not bad, "allowlist entries with no substantive `# reason`: " + ", ".join(bad)
