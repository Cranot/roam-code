"""Per-project config loader (A.0.5).

Reads ``.roam/config.toml`` for tunable knobs — primarily the retrieve
reranker weights, but designed to grow as more commands take config.

Resolution order
----------------
1. ``ROAM_CONFIG`` environment variable (absolute path) — useful for tests
   and CI.
2. ``<project_root>/.roam/config.toml`` — the canonical location.
3. Built-in defaults (no file, no error).

TOML parser
-----------
- Python 3.11+ uses stdlib ``tomllib``.
- Python 3.9 / 3.10 try ``tomli`` if installed.
- Final fallback is a tiny in-tree parser for the subset we actually
  emit (``[section]`` headers + ``key = value`` lines, scalar values).

The fallback keeps the zero-dependency promise that ``_parse_simple_yaml``
already establishes for ``commands/gate_presets.py``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import click

from roam.db.connection import find_project_root

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

#: Retrieve reranker weights. Sum to 1.0 by convention; the reranker
#: normalises in any case. Values were chosen so PageRank dominates,
#: co-change is the second-strongest signal, and the structural / runtime
#: signals refine the result. They become tunable here so the eval harness
#: (A.0.4) can sweep them without code changes.
DEFAULT_RETRIEVE_WEIGHTS: dict[str, float] = {
    "alpha": 0.40,  # personalized PageRank
    "beta": 0.25,  # dark-matter co-change
    "gamma": 0.15,  # inverse layer distance
    "delta": 0.15,  # runtime hotspot signal
    "epsilon": 0.05,  # clone-canonical boost
    "zeta": 0.20,  # v12.2: semantic similarity (bge-small + sqlite-vec)
    # Activates only when [semantic] extras are installed AND the
    # symbol-embedding table is populated; otherwise contributes 0 and
    # the original blend is preserved exactly.
}

DEFAULT_RETRIEVE: dict[str, Any] = {
    **DEFAULT_RETRIEVE_WEIGHTS,
    "default_budget": 4000,
    "default_rerank": "fast",  # 'fast' | 'off' (heavy is post-MVP)
    "default_k": 20,
    # Token-cost heuristic per code line — used for budget accounting.
    # Code averages ~4 tokens per line under modern tokenizers; tunable for
    # workloads with very dense or very sparse files.
    "tokens_per_line": 4,
    # Fixed lexical-baseline weight added to the structural score in the
    # reranker. Keeps lexically-strong but graph-isolated candidates visible.
    # Independent of the alpha/beta/... weight vector.
    "lexical_baseline": 0.5,
    # Cap on the OR fan-out at the FTS5 first stage. A query with 50
    # token-shaped fragments shouldn't emit a 50-clause MATCH expression.
    "first_stage_token_cap": 8,
}

DEFAULTS: dict[str, dict[str, Any]] = {
    "retrieve": DEFAULT_RETRIEVE,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def config_path(project_root: Path | None = None) -> Path:
    """Return the path that ``load_config`` will read.

    Honours ``ROAM_CONFIG`` if set; otherwise resolves to
    ``<project_root>/.roam/config.toml``.
    """
    override = os.environ.get("ROAM_CONFIG")
    if override:
        return Path(override)
    if project_root is None:
        project_root = find_project_root()
    return project_root / ".roam" / "config.toml"


def load_config(project_root: Path | None = None) -> dict[str, dict[str, Any]]:
    """Load ``.roam/config.toml`` merged with defaults.

    Missing file is **not** an error — the function silently returns the
    defaults. Malformed TOML raises ``click.ClickException`` with a clear
    remediation message so agents see actionable output.
    """
    path = config_path(project_root)
    if not path.exists():
        return _deepcopy_defaults()

    try:
        raw = _parse_toml(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — anything from the parser
        raise click.ClickException(
            f"Failed to parse {path}: {exc}\n"
            "  Check the file for unmatched brackets, missing quotes, or "
            "non-scalar values. roam config supports `[section]` headers "
            "with `key = value` scalar entries only."
        ) from exc

    return _merge(_deepcopy_defaults(), raw)


def get_retrieve_weights(project_root: Path | None = None) -> dict[str, float]:
    """Convenience accessor — return the five reranker weights as floats."""
    cfg = load_config(project_root).get("retrieve", {})
    return {key: float(cfg.get(key, default)) for key, default in DEFAULT_RETRIEVE_WEIGHTS.items()}


def get_retrieve_config(project_root: Path | None = None) -> dict[str, Any]:
    """Return the full ``[retrieve]`` section merged with defaults."""
    return dict(load_config(project_root).get("retrieve", {}))


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _deepcopy_defaults() -> dict[str, dict[str, Any]]:
    return {section: dict(values) for section, values in DEFAULTS.items()}


def _merge(base: dict[str, dict[str, Any]], overlay: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Merge a parsed TOML doc into the defaults.

    Top-level scalar keys (no section) are dropped — config is required
    to be sectioned to keep the shape predictable.
    """
    for section, values in overlay.items():
        if not isinstance(values, dict):
            continue
        target = base.setdefault(section, {})
        for key, value in values.items():
            target[key] = value
    return base


def _parse_toml(text: str) -> dict[str, Any]:
    """Parse TOML using the best available backend."""
    try:
        import tomllib  # type: ignore[import-not-found]

        return tomllib.loads(text)
    except ImportError:
        pass
    try:
        import tomli  # type: ignore[import-not-found]

        return tomli.loads(text)
    except ImportError:
        pass
    return _parse_simple_toml(text)


def _parse_simple_toml(text: str) -> dict[str, dict[str, Any]]:
    """Tiny TOML subset parser — sections + scalar key/value pairs.

    Supports:
      - ``[section]`` headers (single level only — no dotted sections)
      - ``key = value`` lines, where value is a quoted string, number,
        boolean, or float.
      - ``#`` line comments (full-line and trailing).
      - Blank lines.

    Raises ``ValueError`` on any unsupported construct (arrays, inline
    tables, multi-line strings, dotted keys).
    """
    out: dict[str, dict[str, Any]] = {}
    section: dict[str, Any] | None = None

    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1].strip()
            if not name or "." in name:
                raise ValueError(f"line {lineno}: dotted/empty section names not supported ({line!r})")
            section = out.setdefault(name, {})
            continue
        if section is None:
            raise ValueError(f"line {lineno}: key/value before any [section] header")
        if "=" not in line:
            raise ValueError(f"line {lineno}: missing '=' in {line!r}")

        key, _, value = line.partition("=")
        key = key.strip()
        value = _strip_inline_comment(value.strip())
        if not key:
            raise ValueError(f"line {lineno}: empty key")
        section[key] = _coerce_scalar(value, lineno)

    return out


def _strip_inline_comment(value: str) -> str:
    """Drop a trailing ``# comment`` unless inside a quoted string."""
    if not value:
        return value
    if value[0] in ('"', "'"):
        # Find matching quote, then ignore everything beyond it.
        quote = value[0]
        end = value.find(quote, 1)
        if end == -1:
            return value
        return value[: end + 1].strip()
    hash_idx = value.find("#")
    if hash_idx >= 0:
        value = value[:hash_idx]
    return value.strip()


def _coerce_scalar(value: str, lineno: int) -> Any:
    if not value:
        raise ValueError(f"line {lineno}: empty value")
    if value[0] in ('"', "'") and value[-1] == value[0] and len(value) >= 2:
        return value[1:-1]
    lower = value.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    try:
        if "." in value or "e" in lower:
            return float(value)
        return int(value)
    except ValueError as exc:
        raise ValueError(f"line {lineno}: cannot parse {value!r} as scalar") from exc
