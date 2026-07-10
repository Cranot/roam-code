#!/usr/bin/env python
"""Fast deterministic pre-push structural-gate bundle.

Runs locally, before ``git push``, the repo-wide structural drift-guards
that CI runs but contributors routinely skip — the exact class of failure
that produced this session's ~14 CI fix-forward cascade. Every gate here
is a pure AST / file / registry scan: NO ``roam`` index build, NO graph
construction, NO network. The whole FAST bundle measures ~43s on a
Windows host (ruff + count scripts ≈ 2s; the structural-lint pytest
bundle ≈ 41s).

Design authority: ``(internal memo)`` (the measured
~43s design + back-test showing this bundle would have caught the dominant
structural-drift fix-forward class). Read that memo before editing the
gate list.

Composition (does NOT duplicate existing hooks):
- ``.githooks/pre-commit`` (W250 / Wave30.1) already runs the two count
  scripts at *commit* time.
- ``.githooks/commit-msg`` + ``.pre-commit-config.yaml`` (Wave59) already
  reject ``Co-Authored-By`` trailers (Cranot-only policy).
This gate's unique value-add is the **structural-lint pytest bundle**
(W547/W564 severity-rank, LAW-4, fragile-path, bare-except, optional-imports,
detector-count, card-hash, compound-recipe) that NO existing hook runs. The ruff + 3 count
scripts are re-run here as a cheap (~2s) backstop for ``--no-verify``
commit-time bypasses.

Usage::

    python scripts/prepush_check.py            # FAST tier (default)
    python scripts/prepush_check.py --fast      # explicit FAST tier
    python scripts/prepush_check.py --full      # FAST + heavy doc-hygiene

Exits non-zero on the first failing gate (after running every gate so the
summary is complete), printing per-gate timing and a copy-pasteable fix
command for each failure.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

# ---------------------------------------------------------------------------
# Repo-root resolution (W572/W588 — never hardcode; git toplevel first, then
# marker-file walk, then historical fallback). Mirrors
# tests/_helpers/repo_root.py so this script stays import-light and usable
# from a git hook without the test package on sys.path.
# ---------------------------------------------------------------------------

_MARKER_FILES = ("CLAUDE.md", "pyproject.toml")


def _has_markers(path: Path) -> bool:
    return all((path / m).exists() for m in _MARKER_FILES)


def _git_toplevel(start: Path) -> Path | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    out = proc.stdout.strip()
    if not out:
        return None
    candidate = Path(out).resolve()
    return candidate if candidate.exists() else None


@lru_cache(maxsize=1)
def repo_root() -> Path:
    """Canonical repo root (directory containing CLAUDE.md + pyproject.toml)."""
    here = Path(__file__).resolve().parent  # scripts/
    toplevel = _git_toplevel(here)
    if toplevel is not None and _has_markers(toplevel):
        return toplevel
    for candidate in (here, *here.parents):
        if _has_markers(candidate):
            return candidate
    # Last-resort: scripts/ -> parent is repo root.
    return here.parent


# ---------------------------------------------------------------------------
# Gate definitions
# ---------------------------------------------------------------------------

# The FAST-tier structural-lint pytest drift-guards. Names ONLY (relative to
# tests/). The drift guard tests/test_prepush_gate_wired.py AST-scans this
# tuple and asserts every entry still exists, so a renamed/deleted guard
# cannot silently drop out of the bundle. Source: PREPUSH-GATE-DESIGN memo
# section 2, FAST tier.
FAST_PYTEST_GUARDS: tuple[str, ...] = (
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
    # 2026-07-10: the 13.8.0 release tripped these full-CI drift-guards ONE
    # per CI round (8 rounds) because none were in this FAST tier. All are
    # in-process AST/registry scans (~35s combined) that fire whenever a new
    # command / cmd file / detector / site-copy ships. Adding them here means
    # the next release catches the whole class in one local run, not N CI
    # rounds. See [[roam-code-ci-campaign]] memo (#166/#168).
    "test_sarif_disclosure_coverage.py",
    "test_mode_classification_coverage.py",
    "test_budget_coverage_survey.py",
    "test_commands_doc_synced.py",
    "test_docs_site_quality.py",
    "test_snake_case_function_lint.py",
    "test_cli_contract.py",
    "test_canonical_constant_citations.py",
)

# FULL-tier additions (heavy doc-hygiene + extra-axis guards). Per the memo,
# test_no_internal_language scans every git-tracked file (~20s) — too heavy
# for FAST, earns its place in FULL.
FULL_PYTEST_GUARDS: tuple[str, ...] = (
    "test_no_internal_language.py",
    "test_w805_qqqqq_compound_recipe_shape_axis_drift.py",
    "test_w1005_smells_severity_parity.py",
)


@dataclass
class GateResult:
    name: str
    passed: bool
    seconds: float
    fix_hint: str = ""
    detail: str = ""


@dataclass
class GateRunner:
    root: Path
    results: list[GateResult] = field(default_factory=list)

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        src = str(self.root / "src")
        current = env.get("PYTHONPATH")
        env["PYTHONPATH"] = src if not current else f"{src}{os.pathsep}{current}"
        return env

    def _run(self, name: str, argv: list[str], fix_hint: str) -> GateResult:
        print(f"[prepush] {name} ...", flush=True)
        start = time.perf_counter()
        proc = subprocess.run(argv, cwd=str(self.root), check=False, env=self._env())
        elapsed = time.perf_counter() - start
        passed = proc.returncode == 0
        result = GateResult(name=name, passed=passed, seconds=elapsed, fix_hint=fix_hint if not passed else "")
        status = "PASS" if passed else "FAIL"
        print(f"[prepush] {name}: {status} ({elapsed:.1f}s)", flush=True)
        self.results.append(result)
        return result

    # -- individual gate groups -------------------------------------------

    def _run_ruff(self) -> None:
        self._run(
            "ruff format --check",
            [sys.executable, "-m", "ruff", "format", "--check", "src/roam", "tests"],
            fix_hint="python -m ruff format src/roam tests",
        )
        self._run(
            "ruff check",
            [sys.executable, "-m", "ruff", "check", "src/roam", "tests"],
            fix_hint="python -m ruff check --fix src/roam tests",
        )

    def _run_leak_gate(self) -> None:
        # Anti-leak gate: the internal-language scan runs in CI
        # (roam-ci.yml) too, but a leak that reaches the public repo
        # before CI catches it is exactly the 2026-05-20 incident — so
        # run it here, so a leak fails the push LOCALLY before anything
        # leaves the machine.
        self._run(
            "scan_internal_language.py --all",
            [sys.executable, "scripts/scan_internal_language.py", "--all"],
            fix_hint="remove the flagged internal-language term (see scripts/internal_language_patterns.py)",
        )

    def _run_count_scripts(self) -> None:
        # Cheap (~2s) backstop for --no-verify commit-time bypasses; the
        # canonical commit-time gate lives in .githooks/pre-commit (W250).
        self._run(
            "sync_surface_counts.py",
            [sys.executable, "scripts/sync_surface_counts.py"],
            fix_hint="python scripts/sync_surface_counts.py --write",
        )
        self._run(
            "build_readme_counts.py --check",
            [sys.executable, "dev/build_readme_counts.py", "--check"],
            fix_hint="python dev/build_readme_counts.py --apply",
        )
        self._run(
            "build_changelog_html.py",
            [sys.executable, "scripts/build_changelog_html.py"],
            fix_hint="python scripts/build_changelog_html.py --write",
        )

    def run_pytest_bundle(self, guards: tuple[str, ...], label: str) -> None:
        argv = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            # -n auto --dist loadfile: parallelize the independent structural
            # guards ACROSS files (each guard file is a pure in-process AST/
            # registry/file scan with no shared mutable fixtures, so file-level
            # distribution is race-free and deterministic). Folding the 8
            # release drift-guards into FAST pushed the bundle over the 2-min
            # shell timeout on -n 0; loadfile brings it back down.
            "-n",
            "auto",
            "--dist",
            "loadfile",
            "-p",
            "no:cacheprovider",
            *[f"tests/{g}" for g in guards],
        ]
        self._run(
            f"pytest structural drift-guards ({label})",
            argv,
            fix_hint="re-run the failing test in isolation: python -m pytest tests/<failing_test>.py -n 0 -q",
        )


def _print_summary(results: list[GateResult]) -> bool:
    total = sum(r.seconds for r in results)
    failures = [r for r in results if not r.passed]
    print("\n" + "=" * 64)
    print(
        f"[prepush] {len(results)} gates run in {total:.1f}s — "
        f"{len(results) - len(failures)} passed, {len(failures)} failed"
    )
    if failures:
        print("[prepush] FAILED gates:")
        for r in failures:
            print(f"  - {r.name}  ({r.seconds:.1f}s)")
            if r.fix_hint:
                print(f"      fix: {r.fix_hint}")
        print("[prepush] push BLOCKED. Resolve the above, or bypass with `git push --no-verify` (deliberate).")
    else:
        print("[prepush] all gates passed — safe to push.")
    print("=" * 64)
    return not failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    tier = parser.add_mutually_exclusive_group()
    tier.add_argument("--fast", action="store_true", help="FAST tier (default): ~43s structural-drift bundle")
    tier.add_argument("--full", action="store_true", help="FULL tier: FAST + heavy doc-hygiene guards (~70s)")
    tier.add_argument(
        "--release",
        action="store_true",
        help=(
            "RELEASE tier: FULL + the ENTIRE test suite (-m 'not slow', "
            "what CI runs) + commit-message scan + doc-consistency + "
            "landing-page linkcheck. Run before ANY push that precedes a "
            "tag — green here means CI will be green. ~15-25 min."
        ),
    )
    args = parser.parse_args(argv)

    release = args.release
    full = args.full or release  # each tier is a superset of the previous

    root = repo_root()
    print(f"[prepush] repo root: {root}")
    print(f"[prepush] tier: {'RELEASE' if release else 'FULL' if full else 'FAST'}")

    runner = GateRunner(root=root)
    runner._run_leak_gate()
    runner._run_ruff()
    runner._run_count_scripts()
    runner.run_pytest_bundle(FAST_PYTEST_GUARDS, "FAST")
    if full:
        runner.run_pytest_bundle(FULL_PYTEST_GUARDS, "FULL")
    if release:
        # The CI fix-forward cascade of 2026-06-10/11 (citation lint, a
        # stale skip-table pin, fixture drift) was caught by CI AFTER the
        # push because local gates ran only the targeted bundles. The
        # release tier closes that gap: what CI runs, runs HERE first.
        runner._run(
            "commit-message leak scan (@{upstream}..HEAD)",
            [sys.executable, "scripts/scan_internal_language.py", "--commits", "@{upstream}..HEAD"],
            fix_hint="reword the offending commit message (git rebase -i) before pushing",
        )
        runner._run(
            "doc-consistency suite",
            [sys.executable, "-m", "pytest", "tests/test_doc_consistency.py", "-q", "-n", "0"],
            fix_hint="version/count literals drifted — run the sync scripts and fix the named spots",
        )
        runner._run(
            "landing-page linkcheck",
            [sys.executable, "scripts/linkcheck.py"],
            fix_hint="fix the dead anchor/link named above",
        )
        runner._run(
            "FULL test suite (-m 'not slow', what CI runs)",
            [sys.executable, "-m", "pytest", "tests/", "-q", "-m", "not slow", "-n", "auto"],
            fix_hint="fix the failing tests — CI runs exactly this surface",
        )

    ok = _print_summary(runner.results)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
