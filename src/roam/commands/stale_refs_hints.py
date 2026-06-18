"""Pluggable, ranked, confidence-tagged rename-hint providers for ``stale-refs``.

A *hint* is a suggested rename target for a missing reference, attributed
to a *provider* and tagged with a confidence band. Providers run in
priority order; the highest-confidence hit wins. ``HIGH``-confidence
hints are the only ones eligible for ``--fix apply``.

Confidence ladder
-----------------

* ``HIGH`` — deterministic evidence (git history attests the rename, or
  a unique basename match in the same directory subtree).
* ``MEDIUM`` — single basename match somewhere in the repo, or single
  directory-prefix match in the symbol graph.
* ``LOW`` — multiple candidates, picked by similarity heuristic.

Adding a provider
-----------------

Subclass :class:`HintProvider` and implement :meth:`hint`. Then register
it in the priority order inside :func:`default_providers`. New providers
should never raise — return ``None`` to abstain.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Hint:
    """A suggested rename target with provenance.

    Attributes
    ----------
    target:
        Repo-relative path the missing reference probably should point at.
    confidence:
        ``HIGH`` / ``MEDIUM`` / ``LOW`` — see module docstring.
    reason:
        One short human-readable phrase used in the report and SARIF.
    source:
        Provider slug (``git-history``, ``basename``, ``symbol-graph``).
    """

    target: str
    confidence: str
    reason: str
    source: str

    @property
    def is_high(self) -> bool:
        return self.confidence == "HIGH"


@dataclass
class HintContext:
    """Per-scan state shared across providers.

    The context is constructed once per ``stale-refs`` invocation and
    passed to every provider. It memoises expensive lookups (git rename
    chains, DB connections) so providers can stay stateless.
    """

    project_root: Path
    basename_idx: dict[str, list[str]]
    git_rename_chain: dict[str, str] = field(default_factory=dict)
    git_rename_chain_loaded: bool = False
    _db_files: list[str] | None = None

    def db_files(self) -> list[str] | None:
        """Lazy-load indexed file paths from ``.roam/roam.db`` (or None)."""
        if self._db_files is not None:
            return self._db_files
        db_path = self.project_root / ".roam" / "roam.db"
        if not db_path.exists():
            self._db_files = []
            return self._db_files
        try:
            conn = sqlite3.connect(str(db_path))
            try:
                rows = conn.execute("SELECT path FROM files").fetchall()
                self._db_files = [r[0] for r in rows]
            finally:
                conn.close()
        except sqlite3.Error:
            self._db_files = []
        return self._db_files


class HintProvider(Protocol):
    """Subclass-or-duck-type contract for a rename-hint provider."""

    name: str

    def hint(self, missing_rel: str, ctx: HintContext) -> Hint | None: ...


# ---------------------------------------------------------------------------
# Git-history provider — deterministic, highest priority
# ---------------------------------------------------------------------------


def _load_rename_chain(project_root: Path, *, timeout: float = 30.0) -> dict[str, str]:
    """One-shot collect every rename in repo history → ``{old: new}`` map.

    We use one ``git log --all --diff-filter=R --name-status`` call instead
    of one call per missing target — for repos with hundreds of dangling
    refs this is the difference between 13s and 0.3s. ``setdefault`` keeps
    the most-recent rename per ``old`` so repeated rewrites don't clobber
    the canonical forward link.

    Returns an empty dict on any error (no git, timeout, parse failure).
    """
    try:
        result = subprocess.run(
            [
                "git",
                "log",
                "--all",
                "--diff-filter=R",
                "--name-status",
                "--pretty=format:",
            ],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return {}
    if result.returncode != 0:
        return {}

    chain: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if not line or not line.startswith("R"):
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        status, old, new = parts
        if not status.startswith("R"):
            continue
        old = old.replace("\\", "/").strip()
        new = new.replace("\\", "/").strip()
        if not old or not new or old == new:
            continue
        # Most recent rename wins (git log streams newest-first).
        chain.setdefault(old, new)
    return chain


def _walk_chain(start: str, chain: dict[str, str], project_root: Path, *, max_hops: int = 8) -> str | None:
    """Walk the rename chain forward until we land on an existing file.

    Cycle-safe (visited set), capped (``max_hops`` to avoid runaway
    pathological histories). Returns the destination path or ``None`` when
    the chain doesn't terminate at an existing file.
    """
    current = start
    visited: set[str] = set()
    for _ in range(max_hops):
        if current in visited:
            return None
        visited.add(current)
        nxt = chain.get(current)
        if nxt is None:
            break
        current = nxt
    if current == start:
        return None
    full = (project_root / current).resolve()
    try:
        full.relative_to(project_root)
    except ValueError:
        return None
    if full.exists():
        return current
    return None


class GitHistoryHintProvider:
    """Use git rename history to attest the canonical new path."""

    name = "git-history"

    def hint(self, missing_rel: str, ctx: HintContext) -> Hint | None:
        if not ctx.git_rename_chain_loaded:
            ctx.git_rename_chain = _load_rename_chain(ctx.project_root)
            ctx.git_rename_chain_loaded = True
        if not ctx.git_rename_chain:
            return None
        target = _walk_chain(missing_rel, ctx.git_rename_chain, ctx.project_root)
        if target is None:
            return None
        return Hint(
            target=target,
            confidence="HIGH",
            reason="rename attested in git history",
            source=self.name,
        )


# ---------------------------------------------------------------------------
# Basename provider — wraps the existing heuristic with confidence banding
# ---------------------------------------------------------------------------


def _shared_directory_prefix(missing_rel: str, candidate: str) -> int:
    """Number of leading directory components *missing_rel* and *candidate* share."""
    miss_parts = missing_rel.split("/")[:-1]
    cand_parts = candidate.split("/")[:-1]
    n = 0
    for a, b in zip(miss_parts, cand_parts):
        if a == b:
            n += 1
        else:
            break
    return n


class BasenameHintProvider:
    """Match by basename. HIGH when there's exactly one match in the same subtree."""

    name = "basename"

    def hint(self, missing_rel: str, ctx: HintContext) -> Hint | None:
        base = os.path.basename(missing_rel)
        if not base:
            return None
        candidates = ctx.basename_idx.get(base, [])
        if not candidates:
            return None
        if len(candidates) == 1:
            only = candidates[0]
            shared = _shared_directory_prefix(missing_rel, only)
            confidence = "HIGH" if shared >= 1 else "MEDIUM"
            return Hint(
                target=only,
                confidence=confidence,
                reason="unique basename match",
                source=self.name,
            )
        # Multiple matches — sort by deepest shared prefix, then shortest path.
        ranked = sorted(
            candidates,
            key=lambda p: (-_shared_directory_prefix(missing_rel, p), len(p)),
        )
        return Hint(
            target=ranked[0],
            confidence="LOW",
            reason=f"basename match (1 of {len(candidates)})",
            source=self.name,
        )


# ---------------------------------------------------------------------------
# Symbol-graph provider — only fires when .roam DB exists
# ---------------------------------------------------------------------------


def _path_similarity(a: str, b: str) -> float:
    """Quick path-aware similarity in [0, 1].

    Combines: shared directory prefix length + basename match. Coarse but
    cheap; we only use it to break ties among DB-backed candidates.
    """
    a_n = a.replace("\\", "/")
    b_n = b.replace("\\", "/")
    if os.path.basename(a_n) == os.path.basename(b_n):
        base = 0.5
    else:
        base = 0.0
    shared = _shared_directory_prefix(a_n, b_n)
    depth = max(1, max(len(a_n.split("/")), len(b_n.split("/"))) - 1)
    return base + 0.5 * (shared / depth)


class SymbolGraphHintProvider:
    """Suggest paths from the indexed file set when a ``.roam`` DB exists."""

    name = "symbol-graph"

    def hint(self, missing_rel: str, ctx: HintContext) -> Hint | None:
        files = ctx.db_files()
        if not files:
            return None
        # Score every indexed file path by path-similarity to the missing one;
        # take the best if it's clearly above noise.
        best_path = None
        best_score = 0.0
        for f in files:
            score = _path_similarity(missing_rel, f)
            if score > best_score:
                best_score = score
                best_path = f
        if best_path is None or best_score < 0.5:
            return None
        confidence = "HIGH" if best_score >= 0.85 else "MEDIUM"
        return Hint(
            target=best_path,
            confidence=confidence,
            reason=f"indexed-file similarity {best_score:.2f}",
            source=self.name,
        )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


# Module-level plug-in registry. Extra providers can be appended via
# ``register_hint_provider`` (in-process) or by setting
# ``ROAM_STALE_REFS_PROVIDERS`` to a comma-separated list of
# ``module:Class`` import specs. Custom providers run AFTER the
# built-ins so they never override deterministic git-history /
# symbol-graph evidence — they're meant for domain-specific fallbacks
# (e.g. a Sphinx-config-aware provider, a vendor-docs lookup, etc.).
_EXTRA_PROVIDERS: list["HintProvider"] = []


def register_hint_provider(provider: "HintProvider") -> None:
    """Register an additional hint provider.

    Idempotent — calling twice with the same instance is a no-op.
    Plug-in providers run AFTER the built-ins; they cannot override a
    HIGH-confidence built-in hint.
    """
    if provider not in _EXTRA_PROVIDERS:
        _EXTRA_PROVIDERS.append(provider)


def _load_env_providers() -> list["HintProvider"]:
    """Load providers from ``ROAM_STALE_REFS_PROVIDERS`` env var.

    Format: ``mypkg.providers:VendorDocsHintProvider,other:Foo``. Each
    spec is resolved with ``importlib`` and instantiated with no
    arguments. Failures are swallowed silently — a typo in the env
    var should never crash the scan.
    """
    spec_list = os.environ.get("ROAM_STALE_REFS_PROVIDERS", "").strip()
    if not spec_list:
        return []
    out: list["HintProvider"] = []
    for spec in spec_list.split(","):
        spec = spec.strip()
        if not spec or ":" not in spec:
            continue
        mod_name, _, cls_name = spec.partition(":")
        try:
            import importlib

            mod = importlib.import_module(mod_name)
            cls = getattr(mod, cls_name, None)
            if cls is None:
                continue
            out.append(cls())
        except (ImportError, AttributeError, TypeError, ValueError):
            continue
    return out


def default_providers() -> list[HintProvider]:
    """Priority order — git history first, then symbol graph, then basename.

    Reorder cautiously: providers can short-circuit on the first ``HIGH``
    confidence hit, so ordering changes the output. Plug-in providers
    appended via ``register_hint_provider`` or
    ``ROAM_STALE_REFS_PROVIDERS`` env var run AFTER the built-ins.
    """
    return [
        GitHistoryHintProvider(),
        SymbolGraphHintProvider(),
        BasenameHintProvider(),
        *_EXTRA_PROVIDERS,
        *_load_env_providers(),
    ]


def best_hint(
    missing_rel: str,
    ctx: HintContext,
    providers: list[HintProvider] | None = None,
) -> Hint | None:
    """Run providers in order; return the first HIGH or, failing that, the first non-None.

    Stable across providers that abstain. Never raises — provider failures
    are absorbed as ``None`` so a flaky git invocation doesn't crash the
    whole report.
    """
    pool = providers if providers is not None else default_providers()
    fallback: Hint | None = None
    for p in pool:
        try:
            h = p.hint(missing_rel, ctx)
        except Exception:
            continue
        if h is None:
            continue
        if h.is_high:
            return h
        if fallback is None:
            fallback = h
    return fallback
