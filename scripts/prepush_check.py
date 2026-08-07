#!/usr/bin/env python
"""Fast deterministic pre-push structural-gate bundle.

Runs locally, before ``git push``, the repo-wide structural drift-guards
that CI runs but contributors routinely skip — the exact class of failure
that produced this session's ~14 CI fix-forward cascade. Every gate here
is a pure AST / file / registry scan: NO ``roam`` index build, NO graph
construction, NO network.

Measured push path on a Windows host, 4895 tracked files / 4820 scanned /
44.5 MB (2026-07-28). The first two run in ``.githooks/pre-push``; the rest
are this script's FAST tier::

    secret_scan.py --pre-push-updates     0.5s   pushed range only
    scan_internal_language --pre-push-..  0.4s   pushed range only
    scan_internal_language.py --all      29.6s   WHOLE TREE
    ruff format --check                   0.1s
    ruff check                            0.1s
    sync_surface_counts.py                1.0s
    build_readme_counts.py --check        1.9s
    build_changelog_html.py               0.2s
    pytest structural drift-guards       44.1s   24 files, 4 xdist workers
                                        -----
                                         77.9s   (end-to-end hook: 78.7s)

That is down from a measured ~113s: the hook used to run its own
byte-identical ``--all`` scan (-36.6s, deleted — see ``_run_leak_gate``)
and the scanner's hashed-term hot path was not memoized (-7.5s, median of
three interleaved A/B runs). The pytest bundle is by far the noisiest gate on a
loaded host: the same 24 files measured 37s and 46s on the same afternoon,
so read a single run of it as a range, not a number.

WHAT A GREEN FAST RUN DOES **NOT** PROVE: that CI will be green. This tier
runs the structural drift-guards, not the test suite. On 2026-07-28 four
commits passed it and then took all four CI lanes red. ``--release`` is the
tier that runs what CI runs; use it before a tag.

Design authority: ``(internal memo)`` (the original
design + back-test showing this bundle would have caught the dominant
structural-drift fix-forward class; its ~43s figure predates the release
drift-guards being folded into FAST). Read that memo before editing the
gate list.

Composition (does NOT duplicate existing hooks):
- ``.githooks/pre-commit`` (W250 / Wave30.1) already runs the two count
  scripts at *commit* time.
- ``.githooks/commit-msg`` + ``.pre-commit-config.yaml`` (Wave59) already
  reject ``Co-Authored-By`` trailers (Cranot-only policy).
- ``.githooks/pre-push`` runs the two *pushed-range* gates (secret scan +
  internal-language scan over the exact ref updates) and then delegates
  here. It does NOT run the whole-tree leak scan of its own; ``--all``
  below is the single whole-tree pass in the push path.
This gate's unique value-add is the **structural-lint pytest bundle**
(W547/W564 severity-rank, LAW-4, fragile-path, bare-except, optional-imports,
detector-count, card-hash, compound-recipe, closed-set registry inventories)
that NO existing hook runs. The ruff + 3 count
scripts are re-run here as a cheap (~2s) backstop for ``--no-verify``
commit-time bypasses.

Usage::

    python scripts/prepush_check.py            # FAST tier (default)
    python scripts/prepush_check.py --fast      # explicit FAST tier
    python scripts/prepush_check.py --full      # FAST + heavy doc-hygiene
    python scripts/prepush_check.py --release --workers 2

Exits non-zero on the first failing gate (after running every gate so the
summary is complete), printing per-gate timing and a copy-pasteable fix
command for each failure.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
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
    # 2026-07-28: four commits passed this hook and then took ALL FOUR CI
    # lanes red. Not flakiness -- four CLOSED-SET registries rejected files
    # added without registering them (.mailmap absent from PUBLIC_ALLOWLIST,
    # a new workflow absent from the closed workflow inventory, a stale curl
    # tally, and a pinned docs phrase reworded across two lines). Every one
    # is a pure file/AST assertion with no index dependency, and folding all
    # four into the existing worker pool measured +0.26s on the bundle
    # (38.79s -> 39.05s). A whole class of CI-red for a quarter of a second.
    "test_public_allowlist.py",
    "test_workflow_dependency_lock_policy.py",
    "test_publish_provenance_workflow.py",
    "test_composite_action_security.py",
    # 2026-08-06: a @click.option the command body never reads is a flag the
    # user passes and we silently discard. scripts/audit_dead_cli_flags.py had
    # existed for that class since the `deps --full` bug, but was advisory by
    # its own docstring and had NO caller here, in .github/workflows/ or in
    # .githooks/ -- so it read as coverage and blocked nothing, and eight more
    # dead flags accumulated behind it. This guard runs the auditor with
    # --fail-on-found and pins its CI wiring, so neither half can quietly go
    # decorative again. Pure AST scan of src/roam/commands + a YAML read; no
    # index dependency.
    "test_dead_cli_flags_gate.py",
    # 2026-08-07: the guard on THIS file was not run by this file. Its own
    # module docstring says it "runs in the FAST bundle itself" -- measured, a
    # collect-only over this tuple returned 0 node ids from it. So every
    # contract it pins (the bundle membership above, the once-and-only-once
    # whole-tree leak scan, and the RELEASE note not disclaiming a gate that
    # runs in every tier) was enforced by CI AFTER the push and by nothing
    # before it -- while the docstring read as local coverage. The file it
    # guards is this one, so a change here is exactly when it should run.
    # Pure AST parse of a single script, no subprocess and no index: 10 tests
    # in 0.70s serial, and it folds into the existing worker pool.
    "test_prepush_gate_wired.py",
)

# FULL-tier additions (heavy doc-hygiene + extra-axis guards). Per the memo,
# test_no_internal_language scans every git-tracked file — too heavy for FAST,
# earns its place in FULL.
#
# KNOWN, DELIBERATE OVERLAP (measured 2026-07-28): that guard's single test
# walks every tracked file through the SAME catalogue as the `--all` CLI gate
# above, so `--full` scans the whole tree twice (31.6s + 29.6s) and `--release`
# three times, since the full suite runs it again. Left in place on purpose:
# `--release` exists to run WHAT CI RUNS, and this file IS the CI gate. Buying
# ~30s on a manual tier by making the release preflight diverge from CI is the
# wrong trade — the push path, where the cost was paid on EVERY push, is the
# one that was worth de-duplicating, and it has been.
FULL_PYTEST_GUARDS: tuple[str, ...] = (
    "test_no_internal_language.py",
    "test_w805_qqqqq_compound_recipe_shape_axis_drift.py",
    "test_w1005_smells_severity_parity.py",
)

_MAX_PYTEST_WORKERS = 4
_NATIVE_THREAD_ENV = (
    "BLIS_NUM_THREADS",
    "GOTO_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "OMP_NUM_THREADS",
    "OMP_THREAD_LIMIT",
    "OPENBLAS_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
)
_NATIVE_THREADS_PER_WORKER = "1"
_GIB = 1024**3
_MIN_RELEASE_TEMP_FREE_GIB = 4
_MIN_RELEASE_TEMP_FREE_GIB_PER_WORKER = 2


def _bounded_worker_count(value: str) -> int:
    """Parse a local xdist budget without permitting host-sized fan-out."""
    try:
        workers = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("workers must be an integer from 1 to 4") from exc
    if not 1 <= workers <= _MAX_PYTEST_WORKERS:
        raise argparse.ArgumentTypeError("workers must be an integer from 1 to 4")
    return workers


def _default_worker_count() -> int:
    """Return a deterministic memory-safe local worker budget."""
    return min(max(os.cpu_count() or 1, 1), _MAX_PYTEST_WORKERS)


def _release_temp_required_bytes(workers: int) -> int:
    """Reserve enough fixture space for one bounded release-suite worker pool."""

    gib = max(_MIN_RELEASE_TEMP_FREE_GIB, workers * _MIN_RELEASE_TEMP_FREE_GIB_PER_WORKER)
    return gib * _GIB


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
    pytest_workers: int = 1
    results: list[GateResult] = field(default_factory=list)

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        # Git invokes hooks with repository-local control variables such as
        # GIT_INDEX_FILE. Pytest gates create foreign repositories and run
        # `git add` inside them; forwarding an outer linked-worktree index
        # redirects those writes into the real repository. Ask Git for its
        # authoritative local-variable vocabulary and remove it at the
        # subprocess boundary. Keep this defense even though the shell hook
        # sanitizes too: prepush_check.py is also invoked directly.
        local_vars = {
            "GIT_ALTERNATE_OBJECT_DIRECTORIES",
            "GIT_COMMON_DIR",
            "GIT_CONFIG",
            "GIT_CONFIG_PARAMETERS",
            "GIT_DIR",
            "GIT_GRAFT_FILE",
            "GIT_IMPLICIT_WORK_TREE",
            "GIT_INDEX_FILE",
            "GIT_OBJECT_DIRECTORY",
            "GIT_REPLACE_REF_BASE",
            "GIT_SHALLOW_FILE",
            "GIT_WORK_TREE",
        }
        try:
            proc = subprocess.run(
                ["git", "-C", str(self.root), "rev-parse", "--local-env-vars"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            if proc.returncode == 0:
                local_vars.update(name.strip() for name in proc.stdout.splitlines() if name.strip())
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass
        for name in local_vars:
            env.pop(name, None)
        # Keep --workers as the total concurrency budget. NumPy/SciPy may load
        # a native math runtime in each xdist worker; inherited host-sized
        # defaults otherwise multiply a bounded worker pool into dozens of
        # threads. Override poisoned outer values as well as absent defaults.
        for name in _NATIVE_THREAD_ENV:
            env[name] = _NATIVE_THREADS_PER_WORKER
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

    def run_release_temp_capacity_gate(self) -> GateResult:
        """Fail before expensive tests when their temp volume lacks headroom."""

        name = "release temp-volume capacity"
        print(f"[prepush] {name} ...", flush=True)
        start = time.perf_counter()
        required = _release_temp_required_bytes(self.pytest_workers)
        passed = False
        detail = ""
        try:
            temp_root = Path(tempfile.gettempdir()).resolve(strict=True)
            if not temp_root.is_dir():
                raise NotADirectoryError(temp_root)
            free = shutil.disk_usage(temp_root).free
            passed = free >= required
            detail = f"temp_root={temp_root} free={free / _GIB:.2f} GiB required={required / _GIB:.2f} GiB"
        except OSError as exc:
            detail = f"temp root unavailable: {exc}"
        elapsed = time.perf_counter() - start
        result = GateResult(
            name=name,
            passed=passed,
            seconds=elapsed,
            fix_hint=(
                "remove abandoned pytest fixture trees or point TEMP/TMP at a volume with enough free space"
                if not passed
                else ""
            ),
            detail=detail,
        )
        print(f"[prepush] {name}: {'PASS' if passed else 'FAIL'} ({elapsed:.1f}s; {detail})", flush=True)
        self.results.append(result)
        return result

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
        #
        # This is the ONLY whole-tree run in the push path, and it must stay
        # whole-tree. The hook's cheap sibling gates scope themselves to the
        # pushed ref updates, which cannot see a leak that a GROWN pattern
        # catalogue newly makes visible in a file this push does not touch.
        # `.githooks/pre-push` ran a byte-identical `--all` invocation of its
        # own until 2026-07-28; over the same unchanged tree, in the same
        # process tree, seconds apart, for a measured 36.6s and zero extra
        # coverage. Deleting it there rather than here is deliberate: this is
        # the single place the gate list lives, and `prepush_check.py --fast`
        # is also run directly, without the hook.
        #
        # tests/test_prepush_gate_wired.py pins the once-and-only-once shape.
        self._run(
            "scan_internal_language.py --all",
            [sys.executable, "scripts/scan_internal_language.py", "--all"],
            fix_hint="remove the flagged internal-language term (see scripts/internal_language_patterns.py)",
        )
        # Tracked-but-ignored gate. Sibling of the scan above and the same
        # failure shape: a file everyone believes is excluded, publishing on
        # every push. The difference is that this one's natural audit --
        # `git ls-files | xargs git check-ignore` -- returns zero hits ALWAYS,
        # because check-ignore skips indexed paths without `--no-index`. So the
        # class survives being "audited clean": measured 2026-08-05, the naive
        # form found 0 in three repos while the correct form found 43 in one
        # (incl. a 2.6 MB personal corpus) and 27 in another (incl. a
        # CREDENTIALS.md tracked since 2025-09-17 under a rule naming it).
        #
        # ~0.5s on this repo (4959 tracked files, median of three runs), no
        # index build, no network. It is index-free by design, so it costs the
        # same on a cold clone. This repo measures CLEAN today -- the gate was
        # wired only after confirming that, so it blocks no existing push.
        #
        # --fail-on-found exits 5 on UNANALYZABLE as well as on VIOLATION: a
        # gate that cannot measure must not report success.
        self._run(
            "roam ignore-drift --fail-on-found",
            [sys.executable, "-m", "roam", "ignore-drift", "--fail-on-found"],
            fix_hint=(
                "a tracked file matches a .gitignore rule; untrack it with "
                "`git rm --cached <path>` (the file stays on disk)"
            ),
        )

    def _run_count_scripts(self) -> None:
        # Cheap (~2s) backstop for --no-verify commit-time bypasses; the
        # canonical commit-time gate lives in .githooks/pre-commit (W250).
        self._run(
            "sync_surface_counts.py",
            [sys.executable, "scripts/sync_surface_counts.py"],
            fix_hint="python scripts/sync_surface_counts.py --write",
        )
        # W1502. The sibling above proves every install pin names the SAME
        # version; this proves the version they name EXISTS. Public main shipped
        # a pip install pin for an unreleased version for 33 commits with the
        # sibling green, because agreeing with pyproject and being installable are different
        # questions. Offline (tag list only) and sub-second, so it belongs here
        # rather than in publish.yml — a truth check that runs after publication
        # cannot prevent the lie it detects.
        self._run(
            "check_install_targets.py",
            [sys.executable, "scripts/check_install_targets.py"],
            fix_hint=(
                "an install instruction names a version with no v<version> tag; "
                "run `python scripts/sync_surface_counts.py --write` to pin install "
                "sites to the last published release. Exit 2 means UNKNOWN — read "
                "the printed reason first: if this clone has no tags (a shallow "
                "clone has none), run `git fetch --tags` and re-run, because the "
                "sync script refuses under that same condition and cannot fix it"
            ),
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
            # A bounded worker pool + loadfile distribution parallelizes the independent structural
            # guards ACROSS files (each guard file is a pure in-process AST/
            # registry/file scan with no shared mutable fixtures, so file-level
            # distribution is race-free and deterministic). Folding the 8
            # release drift-guards into FAST pushed the bundle over the 2-min
            # shell timeout on -n 0; four or fewer workers bring it back down
            # without letting high-core hosts exhaust memory/process slots.
            "-n",
            str(self.pytest_workers),
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


# The CI lanes the RELEASE note disclaims, as DATA rather than as one
# concatenated string literal.
#
# Each entry is ``(probe, text)``. ``probe`` is matched as a substring against
# the NAMES of the gates this run actually recorded; a bullet whose probe
# matches is not printed as unproven, because it was proven. That makes the
# note self-correcting per tier and removes an entire class by construction:
# a lane wired into the push path and left in this list can no longer be
# disclaimed, whatever syntax wires it.
#
# It replaces a source-parsing guard that read `main()` for the gates it runs.
# That guard read 7 of the 8 gates a live FAST run records, and its reach was
# bounded by syntax it could recognise: a keyword-argument call, an aliased
# receiver, a starred argv, a non-literal label (which is what makes the 8th
# gate invisible — `run_pytest_bundle` builds its label with an f-string), a
# module-level helper, or an inline `results.append(...)` all evaded it. Worse,
# a correctly-shaped gate placed under `if release:` evaded it too, and no
# widening of a reader fixes that, because the defect there is in the predicate
# rather than in the parsing. `runner.results` already holds the answer exactly,
# at the moment the note is printed. See tests/test_prepush_gate_wired.py.
_RELEASE_UNPROVEN_LANES: tuple[tuple[str, str], ...] = (
    ("test-exec-bits", "scripts/test-exec-bits.sh + shellcheck   (lint job)"),
    ("strip_metadata", "scripts/strip_metadata.py               (doc-hygiene; needs pypdf)"),
    ("roam compatibility", "roam compatibility --ci / --require-coverage"),
    ("roam secrets", "roam secrets --fail-on-found   (secret-scan; its other 2 of 3 steps DO run here)"),
    ("dependency-audit", "dependency-audit / test-no-optional-deps / wheel-smoke jobs"),
)


def _release_note_lines(results: list[GateResult]) -> tuple[list[str], list[tuple[str, str]]]:
    """Split the disclaimed-lane list against what this run actually ran.

    Returns ``(unproven, proven)`` where ``proven`` pairs each suppressed
    bullet with the gate name that disproved it. The caller prints the
    correction rather than silently shortening the list: an operator who has
    read this note before should see WHY it changed, and a suppression with no
    visible cause is indistinguishable from a bullet someone deleted.
    """
    ran = [r.name for r in results]
    unproven: list[str] = []
    proven: list[tuple[str, str]] = []
    for probe, text in _RELEASE_UNPROVEN_LANES:
        match = next((name for name in ran if probe in name), None)
        if match is None:
            unproven.append(text)
        else:
            proven.append((text, match))
    return unproven, proven


def _print_summary(results: list[GateResult], tier: str = "FAST") -> bool:
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
        print("[prepush] all gates passed - safe to push.")
        if tier != "RELEASE":
            # Honesty, not decoration. On 2026-07-28 four commits passed this
            # tier and then took all four CI lanes red, because the tier runs
            # structural drift-guards and NOT the test suite. An operator who
            # reads "all gates passed" as "CI will be green" is reading
            # something this gate never measured.
            print(
                f"[prepush] NOTE: {tier} tier proves no leak/secret in the push, no ruff or\n"
                "[prepush]   count drift, and no structural-registry drift. It does NOT run\n"
                "[prepush]   the test suite, so it does NOT prove CI will be green.\n"
                "[prepush]   Before a tag: python scripts/prepush_check.py --release"
            )
        else:
            # Same honesty rule as the branch above, applied to the tier that
            # most invites over-reading. RELEASE covers CI's test + ruff +
            # doc-hygiene surface, and that is genuinely most of the
            # fix-forward mass — but "green here means CI will be green" was
            # measurably false: no tier runs these lanes, so a red one of
            # them stays invisible until after the push.
            # Enumerated against .github/workflows/ on 2026-08-06; re-check
            # this list when a CI lane is added. The list is _RELEASE_UNPROVEN_LANES
            # and it is FILTERED against `results` below, so a lane that was
            # wired into the push path corrects itself here instead of being
            # disclaimed by a stale literal. Narrowed 2026-08-07: the
            # `roam secrets` bullet used to also name
            # `roam ignore-drift --fail-on-found`, which `_run_leak_gate` runs
            # unconditionally before any tier branching — 2 of secret-scan.yml's
            # 3 steps run in every tier. That instance is now unrepeatable
            # rather than merely corrected.
            unproven, proven = _release_note_lines(results)
            print("[prepush] NOTE: RELEASE covers CI's test, ruff and doc-hygiene surface.")
            if unproven:
                print("[prepush]   These CI lanes are NOT run by any tier and stay unproven here:")
                for bullet in unproven:
                    print(f"[prepush]     - {bullet}")
                print("[prepush]   Green here does not prove those lanes are green.")
            else:
                print("[prepush]   Every lane this note tracks ran in this tier.")
            for bullet, gate_name in proven:
                # Printed, not dropped. A shorter list with no stated cause
                # reads as an edit; this reads as a measurement.
                print(f"[prepush]   (no longer unproven: {bullet} — ran as {gate_name!r})")
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
            "landing-page linkcheck --strict. Run before ANY push that "
            "precedes a tag. Covers CI's test, lint(ruff) and doc-hygiene "
            "surface; it does NOT run every CI lane (see the RELEASE note "
            "printed on success). ~15-25 min."
        ),
    )
    parser.add_argument(
        "--workers",
        type=_bounded_worker_count,
        default=_default_worker_count(),
        metavar="N",
        help="local pytest workers (1-4; defaults to min(cpu_count, 4))",
    )
    args = parser.parse_args(argv)

    release = args.release
    full = args.full or release  # each tier is a superset of the previous

    root = repo_root()
    print(f"[prepush] repo root: {root}")
    print(f"[prepush] tier: {'RELEASE' if release else 'FULL' if full else 'FAST'}")
    print(f"[prepush] pytest workers: {args.workers} (loadfile distribution)")
    print(f"[prepush] native math threads per worker: {_NATIVE_THREADS_PER_WORKER}")

    tier_label = "RELEASE" if release else "FULL" if full else "FAST"
    runner = GateRunner(root=root, pytest_workers=args.workers)
    if release and not runner.run_release_temp_capacity_gate().passed:
        _print_summary(runner.results, tier_label)
        return 1
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
        # `--strict` is load-bearing, not decoration. `_render_report` ends
        # `return 1 if strict else 0` (scripts/linkcheck.py:151), so the
        # flagless form PRINTS every dead link and then exits 0 — and this
        # runner records PASS on returncode == 0 (`_run`, above). From the
        # tier's first commit (2b60ff3a) until 2026-08-06 this gate therefore
        # could not fail, against both its own design memo and
        # CONTRIBUTING.md:504, which specify the `--strict` form. It
        # went unnoticed because a clean tree exits 0 either way; measured with
        # one injected dead link, flagless exited 0 and `--strict` exited 1.
        # tests/test_linkcheck_gate.py AST-pins the flag into this argv.
        runner._run(
            "landing-page linkcheck",
            [sys.executable, "scripts/linkcheck.py", "--strict"],
            fix_hint="fix the dead anchor/link named above",
        )
        # Mirror CI's index pre-build before fanning out across workers.
        #
        # CI builds the repo index on one lane (roam-ci.yml "Build the repo index
        # (dogfood coverage lane)") and then runs pytest SERIALLY. This gate runs
        # N workers with --dist loadfile and never built the index, so on a cold
        # checkout the first worker to need it starts a build and every other
        # worker that touches an index-backed CLI path fails with "The roam index
        # is currently being built by another process".
        #
        # Measured on a cold 12-core box: 224 failures, 147 of them (66%) that
        # single message. On a warm developer machine it is invisible, which is
        # why the divergence survived -- the gate only bites where the index is
        # absent, and that is exactly the state a fresh clone is in.
        #
        # Build only when missing: on a normal machine this is a no-op, so the
        # gate does not gain the ~2.5 min index cost on every push.
        index_db = Path(".roam") / "index.db"
        if not index_db.exists():
            runner._run(
                "repo index readiness (cold-checkout guard)",
                [sys.executable, "-m", "roam", "index", "--quiet"],
                fix_hint="build the index once: python -m roam index",
            )

        # SERIAL, deliberately — this is the tier that decides a release.
        #
        # This ran in parallel until 2026-07-27 and the parallelism produced only
        # costs, never a finding:
        #   * three consecutive runs died without a terminal result (76%, 91%,
        #     and one at 74%), one with `[gw1] node down: Not properly
        #     terminated` -- a crashed worker aborts the whole gate, and in the
        #     output a crashed worker is indistinguishable from a red suite;
        #   * each worker builds its own pytest fixture tree, so a killed run
        #     strands ~4x the temp. Three of them accumulated 2.2 GB and pushed
        #     the volume from 10 GiB free to 8.0 GiB, making each retry likelier
        #     to fail than the last;
        #   * every failure it surfaced that CI did not was xdist isolation, not
        #     a product defect.
        #
        # CI runs this surface serially on a pre-built index (roam-ci.yml:116).
        # A release gate should verify WHAT CI VERIFIES. Being harsher than the
        # thing you are gating for buys false alarms, and a gate that cannot
        # finish is worse than a slower one -- it invites `--no-verify`, which
        # discards a gate that has caught three real problems in a single day.
        #
        # The parallel form remains available for the FAST/FULL drift-guard
        # bundles above, where the files are pure in-process scans with no
        # shared fixtures and the distribution is genuinely race-free.
        # `-rf` so a failure NAMES the failing tests. Without it the gate reports
        # only "FAIL (6339.6s)" and the operator must re-run a 105-minute suite to
        # learn which nine of ~19,300 tests broke. A gate that tells you it failed
        # but not what failed is barely more useful than one that cannot fail --
        # the same shape as a crashed worker being indistinguishable from a red
        # suite, which is what made the parallel form untenable above.
        runner._run(
            "FULL test suite (-m 'not slow', serial — exactly what CI verifies)",
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/",
                "-q",
                "-m",
                "not slow",
                "-p",
                "no:cacheprovider",
                "-rf",
            ],
            fix_hint=(
                "fix the failing tests — CI runs exactly this surface, serially. "
                "The FAILED lines above name them; re-run just those with "
                "`python -m pytest <nodeid> -q` rather than the whole suite"
            ),
        )

    ok = _print_summary(runner.results, tier_label)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
