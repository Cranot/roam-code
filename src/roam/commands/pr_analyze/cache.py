"""Envelope cache for ``roam pr-analyze`` (D5 split out of cmd_pr_analyze).

A cache hit short-circuits the heavy work — pr-prep + AI scoring + rules
matching — when the inputs that affect the analysis haven't changed.
Inputs hashed: diff text, rules-file content (mtime-independent), block
threshold, language override, the cache schema version, and the identity of
the analyzer code that produced the bundle.

That last term is the one this module was missing. ``CACHE_VERSION`` is a
serialization tag — its own comment scopes it to "when the envelope shape
changes" — so it says nothing about the LOGIC that derived the values inside
that shape. Every other term is an input. With no producer term at all, a
bundle computed by one release was served verbatim to the next under an
identical key, including ``summary.verdict``, which
``cmd_pr_analyze._serve_from_cache`` turns into ``sys.exit(EXIT_GATE_BLOCK)``.
A CI gate could pass or fail a PR on a verdict the running code never
computed and would not agree with.

See :func:`_analyzer_fingerprint` for what the producer term covers and,
just as importantly, what it does not.
"""

from __future__ import annotations

import hashlib
import json as _json
import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from roam.output.formatter import WarningsOut

DEFAULT_CACHE_DIR = Path(".roam") / "pr-analyze-cache"
CACHE_VERSION = 1  # bump when the envelope shape changes

_ANALYZER_FINGERPRINT: str | None = None


def _analyzer_fingerprint() -> str:
    """Identity of the code that computes a pr-analyze bundle.

    Two terms, the same pair the compile-envelope cache settled on after
    measuring a real cross-version serve (``compiler._envelope_cache_key``):

    * **Module mtimes** — every module in ``roam.commands.pr_analyze`` plus
      ``cmd_pr_analyze`` itself, discovered via ``pkgutil`` rather than listed,
      so a module added later is covered without anyone remembering this
      function exists. This is what moves in an editable/dev tree, where a
      logic edit does not touch any version metadata.
    * **Installed version stamp** — reused from ``roam.plan.plan_cache``, not
      re-implemented. mtimes are not enough on their own: deployment schemes
      that normalize timestamps (Nix/Guix/Bazel stores, ``tar --mtime=``,
      SOURCE_DATE_EPOCH-pinned image layers) leave them identical across
      releases, which is exactly how the compile cache was measured serving
      13.9.0's envelope to 13.10.0. Forking a second copy of that stamp would
      make two producers derive their identity two ways, which is the defect
      class this whole term exists to close.

    What it does NOT cover, stated rather than implied: code reached through
    this one that lives elsewhere — the rules engine, pr-prep, the scoring
    model. The rules *file* is content-hashed separately, but a change to the
    engine that interprets it is not seen here. This term makes the common
    upgrade safe; it is not a proof of total coverage.

    When NOTHING can be determined — no readable mtime for any module and no
    version stamp — the fingerprint becomes a per-process token, so the cache
    misses instead of sharing a key across processes on the strength of an
    identity nobody established. A miss costs a recomputation; the alternative
    costs a wrong verdict. Memoized: none of these can change mid-process.
    """
    global _ANALYZER_FINGERPRINT
    if _ANALYZER_FINGERPRINT is not None:
        return _ANALYZER_FINGERPRINT

    import importlib.util
    import pkgutil

    import roam.commands.pr_analyze as _pkg

    names = ["roam.commands.cmd_pr_analyze"]
    names += [f"{_pkg.__name__}.{m.name}" for m in pkgutil.iter_modules(_pkg.__path__)]

    parts: list[str] = []
    resolved = 0
    for name in sorted(set(names)):
        try:
            spec = importlib.util.find_spec(name)
            origin = getattr(spec, "origin", None)
            if not origin:
                parts.append(f"{name}=?")
                continue
            parts.append(f"{name}={int(os.stat(origin).st_mtime)}")
            resolved += 1
        except (OSError, ImportError, ValueError, AttributeError):
            # Unreadable module identity. "?" is a placeholder, NOT an
            # assertion of sameness — the ``resolved`` counter below decides
            # whether enough was established to key a shared cache at all.
            parts.append(f"{name}=?")

    try:
        from roam.plan.plan_cache import _roam_version_stamp

        version = _roam_version_stamp()
    except Exception:  # noqa: BLE001 — a cache key must never break the command
        version = ""
    parts.append(f"version={version}")

    if resolved == 0 and not version:
        # Nothing at all is known about the producer. Fail closed to a
        # per-process key so no two runs can share a cached verdict.
        parts.append(f"unknown={uuid.uuid4().hex}")

    _ANALYZER_FINGERPRINT = ";".join(parts)
    return _ANALYZER_FINGERPRINT


@dataclass(frozen=True)
class _CacheKeyInputs:
    """Value object bundling the inputs that affect a ``pr-analyze`` cache key.

    Owns both the loose-input normalization (``_from_loose``) and the stable
    digest derivation (``digest``) so the cache-key logic lives on the bundled
    type rather than scattered across loose primitive-param functions — the
    value-object realization the ``_cache_key`` adapter defers to.
    """

    diff_text: str
    rules_path: Path
    block_threshold: int
    language_override: str | None

    @classmethod
    def _from_loose(
        cls,
        diff_text: object,
        rules_path: object,
        block_threshold: object,
        language_override: object | None,
    ) -> _CacheKeyInputs:
        """Build a value object from unnormalized primitive inputs."""
        normalized_rules_path = rules_path if isinstance(rules_path, Path) else Path(str(rules_path))
        return cls(
            diff_text="" if diff_text is None else str(diff_text),
            rules_path=normalized_rules_path,
            block_threshold=int(block_threshold),
            language_override=None if language_override is None else str(language_override),
        )

    def digest(self) -> str:
        """Derive the stable sha256 cache key for these inputs.

        Covers the inputs AND the producer — see :func:`_analyzer_fingerprint`
        for why the latter is not optional. Without it every term here is
        something the USER supplied, so upgrading roam left the key fixed and
        the previous release's verdict was served as the current one's.
        """
        h = hashlib.sha256()
        h.update(f"v={CACHE_VERSION}\n".encode())
        h.update(b"analyzer=")
        h.update(_analyzer_fingerprint().encode("utf-8"))
        h.update(b"\ndiff=")
        h.update((self.diff_text or "").encode("utf-8"))
        h.update(b"\nrules=")
        if self.rules_path.exists():
            try:
                h.update(self.rules_path.read_bytes())
            except OSError:
                h.update(b"<unreadable>")
        h.update(f"\nthreshold={self.block_threshold}\n".encode())
        h.update(f"lang={self.language_override or ''}".encode())
        return h.hexdigest()


def _cache_key(inputs: _CacheKeyInputs | object, *legacy_inputs: object) -> str:
    """Derive a stable cache key from inputs that affect the analysis.

    ``_CacheKeyInputs`` is the canonical value object. The variadic branch is a
    compatibility adapter for the historical ``_cache_key(diff, rules,
    threshold, language)`` private import boundary used by tests and callers.
    """
    if isinstance(inputs, _CacheKeyInputs):
        if legacy_inputs:
            raise TypeError("_cache_key(_CacheKeyInputs) accepts no extra arguments")
        return inputs.digest()

    if len(legacy_inputs) != 3:
        raise TypeError(
            "_cache_key expects _CacheKeyInputs or diff_text, rules_path, block_threshold, language_override"
        )

    rules_path, block_threshold, language_override = legacy_inputs
    return _CacheKeyInputs._from_loose(inputs, rules_path, block_threshold, language_override).digest()


def _cache_path(cache_dir: Path, key: str) -> Path:
    return cache_dir / f"{key}.json"


def _load_cache(
    cache_dir: Path,
    key: str,
    *,
    warnings_out: WarningsOut = None,
) -> dict | None:
    """Return cached envelope or None on miss / read error.

    W598: mirrors the W595 ``read_permit`` / W596 ``read_run_meta`` /
    W597 ``daemon_state`` plumb — when *warnings_out* is supplied, every
    silent-error site appends one structured closed-enum marker so
    callers can tell "cache file not on disk" (legitimate cold-cache
    sentinel — does NOT warn, mirrors W597's ``daemon_running`` missing
    PID-file discipline) from "cache file on disk but unreadable" from
    "JSON parsed but top-level not a dict". The ``None`` return on
    every drop path is PRESERVED — the None-return is the caller
    contract (it's how ``_try_cache_envelope`` projects cache-miss).
    ``warnings_out=None`` (default) preserves the pre-W598 silent-drop
    behaviour.

    Marker shape mirrors W595's ``read_permit`` / W596's
    ``read_run_meta`` / W597's ``daemon_state`` closed-enum vocabulary
    with a ``pr_analyze_cache_`` prefix so a caller threading the same
    bucket through multiple substrate read sites sees one uniform
    marker vocabulary.

    Intentional-absence decision (W978 + "Make fallback chains loud"):
    a missing cache file is the documented cold-cache sentinel — the
    common, expected path on first invocation. Warning here would train
    operators to ignore real warnings. The behaviour mirrors W597's
    ``daemon_running`` missing-pidfile discipline (legitimate "not
    running" → no warning) rather than W596's ``read_run_meta``
    missing-meta.json discipline (an operational anomaly worth
    surfacing). Schema-version mismatch is folded into ``_cache_key``
    so a version bump produces a different filename — there's no
    SchemaVersionMismatch path to surface at read time.

    Emitted kinds (closed enum):

      * ``pr_analyze_cache_read_failed:<path>:<exc_class>:<detail>`` —
        ``Path.read_text`` raised ``OSError`` (typically
        ``PermissionError`` / ``IsADirectoryError`` / generic
        ``OSError``). The cache file is on disk but unreadable.
      * ``pr_analyze_cache_corrupt:<path>:JSONDecodeError`` — the
        bytes parsed as something other than JSON.
      * ``pr_analyze_cache_corrupt:<path>:NotAJsonObject`` — JSON
        parsed cleanly but the top-level value was not a dict (the
        downstream ``_try_cache_envelope`` callsite indexes
        ``cached["cache_hit"]`` and ``cached.get("summary")``, so a
        non-dict cached payload is cache poisoning, not cold cache).
    """

    def _emit(kind: str) -> None:
        if warnings_out is not None:
            warnings_out.append(kind)

    p = _cache_path(cache_dir, key)
    if not p.exists():
        # Legitimate cold-cache sentinel — do NOT warn (mirrors W597's
        # ``daemon_running`` missing-pidfile discipline).
        return None
    try:
        raw = _json.loads(p.read_text(encoding="utf-8"))
    except OSError as exc:
        _emit(f"pr_analyze_cache_read_failed:{p}:{type(exc).__name__}:{exc}")
        return None
    except _json.JSONDecodeError:
        _emit(f"pr_analyze_cache_corrupt:{p}:JSONDecodeError")
        return None
    if not isinstance(raw, dict):
        _emit(f"pr_analyze_cache_corrupt:{p}:NotAJsonObject")
        return None
    return raw


def _save_cache(cache_dir: Path, key: str, bundle: dict) -> None:
    """Persist envelope to the cache. Best-effort; a failed write is
    noted on stderr and never raises — the next run just re-analyzes.

    The write path deliberately does NOT thread ``warnings_out`` (W598
    scoped that plumb to the cache READER; the guard
    ``test_save_cache_untouched`` pins it), so visibility here is a
    one-line stderr note in the ``_load_cache`` marker vocabulary
    (``<path>:<exc_class>:<detail>``) — stderr keeps the JSON envelope
    on stdout clean.
    """
    p = _cache_path(cache_dir, key)
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        p.write_text(_json.dumps(bundle, indent=2), encoding="utf-8")
    except (OSError, TypeError, ValueError) as exc:
        sys.stderr.write(f"[pr-analyze] cache write skipped: {p}: {type(exc).__name__}: {exc}\n")
