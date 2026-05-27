"""command_graph — extract + classify the repo's OWN runnable commands (G2).

Evidence-backed, local, deterministic. The engine behind `roam commands`, the
G3 minimal-verification contract, and the **Agent Change Proof Bundle**'s
"checks available / required / ran" sections (see
`internal/planning/PROOF-BUNDLE-SCHEMA-2026-05-27.md`).

Roam's moat is not just that it KNOWS the test command — it can PROVE why it
knows (every command carries `evidence`). This module reuses
`output/project_shape.py`'s hint tables so there is ONE source of truth for
runner / package-manager detection; consolidating
`project_shape._detect_test_runner` + `cmd_agent_export._detect_build_command`
to delegate here is the planned follow-up (this module is the canonical target).

Scope of THIS slice: root `package.json` scripts, `Makefile`, `justfile`,
`pyproject`/`tox`/pytest, and single-ecosystem fallbacks (go/cargo/gem). Nested
workspaces, `turbo.json`/`nx.json`, and CI-workflow `run:` steps are documented
follow-ups (the schema already leaves room — `workspace`, `ci_only`).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from roam.output.project_shape import (
    _BUILD_TOOL_HINTS,
    _PACKAGE_MANAGER_LOCKFILES,
    _TEST_RUNNER_HINTS,
)

__all__ = [
    "COMMAND_GRAPH_SCHEMA_VERSION",
    "KINDS",
    "SCOPES",
    "COSTS",
    "build_command_graph",
    "detect_package_manager",
]

COMMAND_GRAPH_SCHEMA_VERSION = "1.0"

# Closed enums (validated at fact-construction; unknown raises ValueError).
KINDS = frozenset({"test", "typecheck", "lint", "build", "run", "other"})
SCOPES = frozenset({"repo", "package", "file"})
COSTS = frozenset({"low", "medium", "high"})

# Ordered kind classifiers (first match wins). Test reuses project_shape's
# runner needles so the two detectors agree by construction.
_TEST_NEEDLES = tuple(n for _runner, needles in _TEST_RUNNER_HINTS for n in needles) + ("test",)
_TYPECHECK_NEEDLES = ("tsc", "typecheck", "type-check", "type:check", "mypy", "pyright", "tsgo")
_LINT_NEEDLES = ("lint", "eslint", "ruff", "flake8", "clippy", "rubocop", "biome", "prettier --check", "fmt --check", "format:check")
_BUILD_NEEDLES = ("build", "compile", "tsc -b", "tsc --build", "bundle", "webpack", "rollup", "vite build", "cargo build", "go build", "make ")
_RUN_NEEDLES = ("start", "dev", "serve", "preview", "watch", "run ", "exec ")

# Runners whose invocation accepts a path arg (so a targeted check is possible).
_TARGETABLE_NEEDLES = ("pytest", "vitest", "jest", "mocha", "playwright test", "go test", "cargo test", "phpunit", "ava")
# State-mutating / side-effecting verbs — never auto-run.
_MUTATE_NEEDLES = ("deploy", "publish", "release", "migrate", "db:push", "db push", "prisma migrate", "seed", "push", "upload")
_NETWORK_NEEDLES = ("install", "deploy", "publish", "fetch", "download", "docker push", "docker pull", "npm publish", "curl", "wget")

# runner -> corroborating config-file globs (adds evidence + confidence).
_RUNNER_CONFIG_GLOBS = {
    "vitest": ("vitest.config.*", "vite.config.*"),
    "jest": ("jest.config.*", "jest.setup.*"),
    "playwright": ("playwright.config.*",),
    "cypress": ("cypress.config.*",),
    "mocha": (".mocharc.*",),
    "pytest": ("pytest.ini", "tox.ini", "pyproject.toml", "setup.cfg"),
}


def detect_package_manager(root: Path) -> str | None:
    """Package manager from lockfile (reuses project_shape's table)."""
    for name, lockfile in _PACKAGE_MANAGER_LOCKFILES:
        if (root / lockfile).exists():
            return name
    return None


def _script_invocation(pm: str | None, script: str) -> str:
    """How a JS package-manager runs a named script."""
    if pm == "pnpm":
        return f"pnpm {script}"
    if pm == "yarn":
        return f"yarn {script}"
    if pm == "bun":
        return f"bun run {script}"
    return f"npm run {script}"


def _classify_kind(name: str, body: str) -> str:
    """Classify a command by its body (script name as a weak booster)."""
    text = f"{name} {body}".lower()
    # Order matters: typecheck/lint before build before test before run, because
    # a "build" script may invoke tsc; but a "test" script is unambiguous first.
    if any(n in text for n in _TEST_NEEDLES):
        return "test"
    if any(n in text for n in _TYPECHECK_NEEDLES):
        return "typecheck"
    if any(n in text for n in _LINT_NEEDLES):
        return "lint"
    if any(n in text for n in _BUILD_NEEDLES):
        return "build"
    if any(n in text for n in _RUN_NEEDLES):
        return "run"
    return "other"


_COST_BY_KIND = {"test": "high", "build": "high", "typecheck": "medium", "lint": "medium", "run": "low", "other": "medium"}


def _make_fact(
    *,
    fact_id: str,
    command: str,
    kind: str,
    scope: str,
    source: str,
    evidence: list[str],
    confidence: float,
    workspace: str | None = None,
    ci_only: bool = False,
    classify_text: str | None = None,
) -> dict[str, Any]:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {sorted(KINDS)}, got {kind!r}")
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {sorted(SCOPES)}, got {scope!r}")
    cost = _COST_BY_KIND[kind]
    # Heuristic flags read the UNDERLYING command (script body), not the
    # package-manager-prefixed surface form: `pnpm test` hides that the body is
    # `vitest run` (targetable), and a `deploy` script's body may not contain
    # "deploy". ``classify_text`` (name + body) carries both signals.
    low = (classify_text or command).lower()
    mutates = any(n in low for n in _MUTATE_NEEDLES)
    network = any(n in low for n in _NETWORK_NEEDLES)
    targetable = any(n in low for n in _TARGETABLE_NEEDLES)
    long_running = any(n in low for n in ("dev", "serve", "watch", "preview", "start"))
    safe_to_auto_run = (kind in {"test", "typecheck", "lint", "build"}) and not mutates and not long_running
    return {
        "id": fact_id,
        "command": command,
        "kind": kind,
        "scope": scope,
        "workspace": workspace,
        "cost": cost,
        "confidence": round(confidence, 2),
        "evidence": evidence,
        "source": source,
        "safe_to_auto_run": safe_to_auto_run,
        "mutates_state": mutates,
        "requires_network": network,
        "ci_only": ci_only,
        "targetable": targetable,
    }


def _corroborating_evidence(root: Path, command: str) -> list[str]:
    """Config files that corroborate a command's runner (adds provenance)."""
    out: list[str] = []
    low = command.lower()
    for runner, globs in _RUNNER_CONFIG_GLOBS.items():
        if runner in low or any(runner in n for n in (low,)):
            for g in globs:
                for hit in sorted(root.glob(g)):
                    out.append(hit.name)
                    break
    return out


def _extract_package_json(root: Path) -> list[dict[str, Any]]:
    pkg = root / "package.json"
    if not pkg.exists():
        return []
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    scripts = data.get("scripts") if isinstance(data, dict) else None
    if not isinstance(scripts, dict):
        return []
    pm = detect_package_manager(root)
    facts: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for name, body in scripts.items():
        if not isinstance(name, str) or not isinstance(body, str):
            continue
        kind = _classify_kind(name, body)
        command = _script_invocation(pm, name)
        evidence = [f"package.json:scripts.{name}"]
        corrob = _corroborating_evidence(root, body)
        evidence.extend(f"{c}" for c in corrob)
        confidence = 0.9 + (0.05 if corrob else 0.0)
        fact_id = f"{kind}.{name}"
        if fact_id in seen_ids:
            fact_id = f"{kind}.{name}.{len(facts)}"
        seen_ids.add(fact_id)
        facts.append(
            _make_fact(
                fact_id=fact_id,
                command=command,
                kind=kind,
                scope="repo",
                source="package.json",
                evidence=evidence,
                confidence=min(confidence, 0.97),
                classify_text=f"{name} {body}",
            )
        )
    return facts


_MAKE_TARGET_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)\s*:(?!=)")


def _extract_makefile(root: Path) -> list[dict[str, Any]]:
    mk = root / "Makefile"
    if not mk.exists():
        return []
    try:
        lines = mk.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    facts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ln in lines:
        m = _MAKE_TARGET_RE.match(ln)
        if not m:
            continue
        target = m.group(1)
        if target in (".PHONY", "default") or target in seen:
            continue
        seen.add(target)
        kind = _classify_kind(target, target)
        facts.append(
            _make_fact(
                fact_id=f"{kind}.make.{target}",
                command=f"make {target}",
                kind=kind,
                scope="repo",
                source="Makefile",
                evidence=[f"Makefile:{target}"],
                confidence=0.85,
            )
        )
    return facts


_JUST_RECIPE_RE = re.compile(r"^([a-z0-9][a-z0-9_-]*)(?:\s+[^:=]*)?:(?!=)", re.IGNORECASE)


def _extract_justfile(root: Path) -> list[dict[str, Any]]:
    for fname in ("justfile", "Justfile", ".justfile"):
        jf = root / fname
        if jf.exists():
            break
    else:
        return []
    try:
        lines = jf.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    facts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ln in lines:
        if ln.startswith((" ", "\t", "#", "@")):
            continue
        m = _JUST_RECIPE_RE.match(ln)
        if not m:
            continue
        recipe = m.group(1)
        if recipe in seen:
            continue
        seen.add(recipe)
        kind = _classify_kind(recipe, recipe)
        facts.append(
            _make_fact(
                fact_id=f"{kind}.just.{recipe}",
                command=f"just {recipe}",
                kind=kind,
                scope="repo",
                source=jf.name,
                evidence=[f"{jf.name}:{recipe}"],
                confidence=0.85,
            )
        )
    return facts


def _extract_python_and_fallbacks(root: Path, already: bool) -> list[dict[str, Any]]:
    """Single-ecosystem fallbacks — only when no explicit scripts were found."""
    if already:
        return []
    facts: list[dict[str, Any]] = []
    if any((root / f).exists() for f in ("pyproject.toml", "setup.cfg", "tox.ini", "pytest.ini")):
        ev = [f for f in ("pytest.ini", "tox.ini", "pyproject.toml", "setup.cfg") if (root / f).exists()]
        facts.append(_make_fact(fact_id="test.pytest", command="pytest", kind="test", scope="repo",
                                source=ev[0], evidence=ev, confidence=0.6))
    elif (root / "go.mod").exists():
        facts.append(_make_fact(fact_id="test.go", command="go test ./...", kind="test", scope="repo",
                                source="go.mod", evidence=["go.mod"], confidence=0.6))
        facts.append(_make_fact(fact_id="build.go", command="go build ./...", kind="build", scope="repo",
                                source="go.mod", evidence=["go.mod"], confidence=0.6))
    elif (root / "Cargo.toml").exists():
        facts.append(_make_fact(fact_id="test.cargo", command="cargo test", kind="test", scope="repo",
                                source="Cargo.toml", evidence=["Cargo.toml"], confidence=0.6))
    elif (root / "Gemfile").exists():
        facts.append(_make_fact(fact_id="test.rspec", command="bundle exec rspec", kind="test", scope="repo",
                                source="Gemfile", evidence=["Gemfile"], confidence=0.6))
    return facts


def build_command_graph(root: Path | str | None = None) -> dict[str, Any]:
    """Return the repo's classified, evidence-backed command graph.

    ``{"commands": [fact...], "sources_scanned": [...], "package_manager": ...,
       "schema_version": "1.0"}``. Pure + local + deterministic — no DB, no model,
       no network. Unknown root or no manifests → a non-empty envelope with an
       empty ``commands`` list (Pattern 1).
    """
    root = Path(root) if root is not None else Path.cwd()
    sources_scanned: list[str] = []
    commands: list[dict[str, Any]] = []

    pkg_facts = _extract_package_json(root)
    if (root / "package.json").exists():
        sources_scanned.append("package.json")
    commands.extend(pkg_facts)

    for extractor, marker in ((_extract_makefile, "Makefile"), (_extract_justfile, "justfile")):
        facts = extractor(root)
        if facts:
            sources_scanned.append(marker)
        commands.extend(facts)

    fallback = _extract_python_and_fallbacks(root, already=bool(commands))
    if fallback:
        sources_scanned.append(fallback[0]["source"])
    commands.extend(fallback)

    # Deterministic ordering: kind, then id.
    commands.sort(key=lambda c: (c["kind"], c["id"]))
    return {
        "commands": commands,
        "sources_scanned": sources_scanned,
        "package_manager": detect_package_manager(root),
        "schema_version": COMMAND_GRAPH_SCHEMA_VERSION,
    }
