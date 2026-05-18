"""Load and apply ``.roam/smells.suppress.yml`` allowlist for code smells.

W658 (W646 drive-by). Mirrors the discipline of the W125 vibe-check
suppression substrate and the existing ``.roam-suppressions.yml`` triage
file (``suppression.py``), but specialised for code-smell findings.

Why a separate substrate from ``.roam-suppressions.yml``?

* ``.roam-suppressions.yml`` keys by ``(rule, file [, line])`` — fine for
  per-file rule overrides like "secret-detection is a fixture here".
* Smells suppression needs **per-symbol** disambiguation: shotgun-surgery
  on ``roam.languages.registry.get_language_for_file`` is a public-API
  hub by design, but shotgun-surgery on ``cmd_health.health`` would be a
  real refactor candidate. Same kind, same file, different symbol — the
  ``(rule, file)`` key collapses the two.

Format
------

::

    # .roam/smells.suppress.yml
    suppressions:
      - kind: shotgun-surgery
        symbol: roam.languages.registry.get_language_for_file
        reason: "Public API hub by design — fan-in is intended"
        expires: "2026-12-01"   # optional; ISO date
      - kind: shotgun-surgery
        symbol: roam.languages.registry.get_extractor
        reason: "Public API hub by design — registry dispatch"

Matching
--------

A finding is suppressed when **all** of the following hold:

* ``kind`` equals the finding's ``smell_id``
* ``symbol`` equals the finding's ``symbol_name`` (bare name match — the
  smells detector emits bare names; qualified names are accepted and the
  trailing segment is compared)
* if ``expires`` is set, today's date is <= ``expires`` (in UTC); an
  expired suppression is treated as absent

Optional fields ``file``, ``reason``, ``author``, ``added`` are recorded
on the suppression entry for audit but do not influence matching unless
``file`` is set, in which case the finding's file path must end with that
suffix (forward-slash-normalised).

No PyYAML dependency — the parser is intentionally minimal and matches
the structured-list shape documented above.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from roam.output.formatter import WarningsOut
from roam.policy.suppression_v2 import KindSymbolSuppression

DEFAULT_SUPPRESS_PATH = Path(".roam") / "smells.suppress.yml"

# W994 (W918 closed-set discipline): single source of truth for the ISO date
# format used by ``expires`` parsing + the warning text that names the
# expected shape. Both ``_is_expired`` and the load-time validator in
# ``_parse_smells_suppress_yaml`` quote this constant so a future format
# change touches one line.
EXPIRES_FMT = "%Y-%m-%d"


# ---------------------------------------------------------------------------
# YAML parser (mirrors suppression.py — no PyYAML)
# ---------------------------------------------------------------------------


def _known_smell_kinds() -> frozenset[str]:
    """Return the closed set of registered smell kinds + rollup ids (W987).

    Imported lazily so this module stays importable even when the registry
    has not been populated yet (e.g. tooling that imports the parser without
    loading ``roam.catalog.smells``). The set is rebuilt on every call so
    plugin-registered detectors land here without needing a cache bust —
    the cost is one dict copy, negligible against the file-read it gates.
    """
    # Side-effect import: ``roam.catalog.smells`` registers every
    # in-tree detector via ``@detector`` decorators at module load.
    # Mirrors the W941 discipline in cmd_smells.py.
    import roam.catalog.smells  # noqa: F401
    from roam.catalog.registry import kind_to_confidence

    return frozenset(kind_to_confidence().keys())


def _parse_smells_suppress_yaml_root_dict(text: str) -> dict:
    """Tiny YAML parser returning the documented root-mapping shape.

    Returns ``{"suppressions": [rows]}`` so the W1019b helper-driven
    ``load_smells_suppressions`` path can hand the parse output to
    :func:`load_yaml_with_warnings` (which root-type-checks a mapping)
    without the helper rejecting a top-level list. Each row in the
    ``suppressions`` value is the raw dict the legacy parser produced
    (missing-field rows kept for the W995 index-naming pass; expires
    value preserved verbatim so the W994 validator sees unparseable
    dates rather than the strict-coerced ``date`` PyYAML would emit).

    Domain-permissive on purpose — see :func:`_validate_smells_suppress_rows`.
    """
    rows: list[dict] = []
    current: dict | None = None
    in_block = False

    for raw_line in text.split("\n"):
        stripped = raw_line.strip()

        # Skip blank lines and comments
        if not stripped or stripped.startswith("#"):
            continue

        # Detect the top-level "suppressions:" key
        if stripped == "suppressions:":
            in_block = True
            continue

        if not in_block:
            continue

        if stripped.startswith("- "):
            # New row — commit the previous in-progress dict to the row
            # ledger before starting the next one.
            if current is not None:
                rows.append(current)
            current = {}
            rest = stripped[2:].strip()
            if ":" in rest:
                key, _, val = rest.partition(":")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if val:
                    current[key] = val
        elif current is not None and ":" in stripped:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if val:
                current[key] = val

    # Don't forget the last entry
    if current is not None:
        rows.append(current)

    return {"suppressions": rows}


def _validate_smells_suppress_rows(
    rows: list[dict],
    *,
    warnings_out: WarningsOut = None,
    source_path: str | None = None,
) -> list[dict]:
    """Apply W987 / W994 / W995 schema validation to parsed suppression rows.

    Returns the survivors (rows with both ``kind`` AND ``symbol``).

    W987 (Pattern 2 — silent fallback): unknown ``kind`` values append a
    warning naming the offending kind. The entry IS still returned
    (backward compat with rows that were silently kept pre-W987).

    W994 (Pattern 2 — silent default on ``expires``): unparseable
    ``expires`` values append a warning. Semantics preserved as
    "treat as never-expires" (W377-W382 contract); only visibility changes.

    W995 (Pattern 2 — silent malformed-entry drop): every dropped row
    (missing ``kind`` OR ``symbol``) appends a warning naming the 1-based
    index + the missing field. A roll-up count line follows when >1 rows
    are dropped.
    """
    suppressions: list[dict] = []
    # Partition rows into valid + dropped so W995 can name each dropped
    # row by its 1-based index in the source file.
    dropped: list[tuple[int, dict, str]] = []
    for idx, row in enumerate(rows, start=1):
        if "kind" not in row or "symbol" not in row:
            missing = "kind" if "kind" not in row else "symbol"
            dropped.append((idx, row, missing))
            continue
        suppressions.append(row)

    if warnings_out is not None:
        loc = source_path or ".roam/smells.suppress.yml"

        if suppressions:
            known_kinds = _known_smell_kinds()
            for entry in suppressions:
                kind = entry.get("kind") or ""
                if kind and kind not in known_kinds:
                    symbol = entry.get("symbol") or "<unset>"
                    warnings_out.append(
                        f"Edit {loc}: unknown smell kind {kind!r} on entry "
                        f"symbol={symbol!r} matches 0 detectors; "
                        f"fix the kind to match one of the {len(known_kinds)} registered kinds"
                    )

                # W994: validate ``expires`` at load time so the silent
                # never-expires default surfaces an actionable warning.
                # Semantics are preserved — we only WARN, we don't raise
                # or rewrite the entry. ``_is_expired`` keeps its lenient
                # fallback so existing behaviour is byte-stable.
                exp = entry.get("expires")
                if exp:
                    try:
                        datetime.strptime(str(exp), EXPIRES_FMT)
                    except ValueError:
                        symbol = entry.get("symbol") or "<unset>"
                        kind = entry.get("kind") or "<unset>"
                        warnings_out.append(
                            f"Edit {loc}: suppression entry symbol={symbol!r} "
                            f"kind={kind!r} has unparseable expires={str(exp)!r}; "
                            f"expected {EXPIRES_FMT!r}; treating as never-expires "
                            f"until you replace the value with a valid date on one "
                            f"of the suppression entries."
                        )

        # W995: surface every dropped malformed row. Each warning names
        # the 1-based row index + the missing required field so the user
        # can locate the offender directly.
        for idx, _row, missing in dropped:
            warnings_out.append(
                f"Edit {loc}: suppression entry #{idx} dropped — missing "
                f"required field {missing!r}; each row must declare both "
                f"'kind' and 'symbol' fields."
            )
        if len(dropped) > 1:
            warnings_out.append(
                f"Edit {loc}: dropped {len(dropped)} malformed suppression entries "
                f"total; fix the listed rows to restore them."
            )

    return suppressions


def _parse_smells_suppress_yaml(
    text: str,
    *,
    warnings_out: WarningsOut = None,
    source_path: str | None = None,
) -> list[dict]:
    """Parse a ``.roam/smells.suppress.yml`` file into a list of dicts.

    Expected shape::

        suppressions:
          - kind: shotgun-surgery
            symbol: get_language_for_file
            reason: "..."
            expires: "2026-12-01"

    Composition of :func:`_parse_smells_suppress_yaml_root_dict` (the tiny
    parser) and :func:`_validate_smells_suppress_rows` (W987/W994/W995
    schema validation). Kept as a public entry point so library callers
    that bypass the file-path-aware loader still get the validation pass.
    """
    parsed = _parse_smells_suppress_yaml_root_dict(text)
    rows = parsed.get("suppressions", [])
    return _validate_smells_suppress_rows(rows, warnings_out=warnings_out, source_path=source_path)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_smells_suppressions(
    project_root: str | Path,
    *,
    warnings_out: WarningsOut = None,
) -> list[dict]:
    """Load suppressions from ``.roam/smells.suppress.yml`` under *project_root*.

    Returns an empty list when the file is absent or unreadable.
    Each suppression dict has at least ``kind`` and ``symbol`` keys, and
    optionally ``file``, ``reason``, ``expires``, ``author``, ``added``.

    W987 (Pattern 2 — silent fallback): when *warnings_out* is supplied,
    entries with unknown ``kind`` values append a user-actionable warning
    naming the offending kind. The entry IS still returned so the parsed
    list shape is byte-stable with the pre-W987 behaviour. Default ``None``
    keeps the silent-skip semantics for library callers that have no
    envelope to populate.

    W1019b (Phase 2 of the YAML-loader consolidation): the file-read +
    parser-fallback + root-type check now live in
    :func:`roam.commands._yaml_loader.load_yaml_with_warnings`. The
    schema validator (W987/W994/W995 row checks) stays here because the
    on-disk format vocabulary belongs at the callsite. ``force_tiny_parser=True``
    (W1040) routes parsing through :func:`_parse_smells_suppress_yaml_root_dict`
    as the sole engine — PyYAML's strict YAML-1.1 timestamp coercion would
    reject values like ``expires: 2026-13-01`` before the W994 validator
    gets to surface them.
    """
    from roam.commands._yaml_loader import load_yaml_with_warnings

    root = Path(project_root)
    path = root / DEFAULT_SUPPRESS_PATH

    pre_warnings = len(warnings_out) if warnings_out is not None else 0
    data = load_yaml_with_warnings(
        path,
        tiny_parser=_parse_smells_suppress_yaml_root_dict,
        force_tiny_parser=True,
        config_label="smells-suppressions",
        warnings_out=warnings_out,
    )
    if data is None:
        # Missing file — default state, no warning emitted by the helper.
        return []
    if warnings_out is not None and len(warnings_out) > pre_warnings:
        # Helper already explained the failure (read error / malformed
        # parse / wrong root type). Propagate the empty result without
        # piling on validator warnings that would confuse the caller.
        return []
    assert isinstance(data, dict)
    rows = data.get("suppressions", [])
    if not isinstance(rows, list):
        return []
    return _validate_smells_suppress_rows(rows, warnings_out=warnings_out, source_path=str(path))


def load_smells_suppressions_typed(
    project_root: str | Path,
    *,
    warnings_out: WarningsOut = None,
) -> list[KindSymbolSuppression]:
    """Typed counterpart of :func:`load_smells_suppressions` (W692 Phase B-a).

    Returns the same on-disk rows as :class:`KindSymbolSuppression` instances
    instead of raw dicts. The legacy ``load_smells_suppressions`` stays the
    canonical entry point until every caller migrates — this function is the
    bridge new code should reach for.

    Mirrors the Phase A pattern shipped in
    :func:`roam.commands.suppression.load_suppressions_typed`. The internal
    representation is the dataclass; the dict-shaped public API (matcher
    + applier below) is preserved for back-compat per W722.

    W987: ``warnings_out`` is plumbed through so unknown-kind entries
    surface to the CLI envelope. Same semantics as the dict-shaped loader.
    """
    return [
        KindSymbolSuppression.from_dict(d, warnings_out=warnings_out)
        for d in load_smells_suppressions(project_root, warnings_out=warnings_out)
    ]


def _is_expired(
    entry: dict,
    *,
    today: date | None = None,
    warnings_out: WarningsOut = None,
    source_path: str | None = None,
) -> bool:
    """Return True if the suppression's ``expires`` date is in the past.

    A missing or unparseable ``expires`` is treated as "never expires".
    Comparison uses UTC date.

    W994 (Pattern 2 — silent default): when *warnings_out* is supplied,
    an unparseable ``expires`` value appends an actionable warning that
    names the offending string + the suppression's symbol/kind, so a user
    writing ``expires: tomorrow`` (or any non-YYYY-MM-DD string) is no
    longer granted a permanent suppression silently. The semantic stays
    "treat as never-expires" — W377-W382 permit-persist absorbed that
    contract — only the visibility changes. The load-time validator in
    :func:`_parse_smells_suppress_yaml` surfaces the same condition once
    per file load; this match-time plumb is the belt-and-braces hook for
    callers that build entries in memory without going through the
    parser (e.g. tests, programmatic builders).
    """
    exp = entry.get("expires")
    if not exp:
        return False
    try:
        # Accept "YYYY-MM-DD" form.
        exp_date = datetime.strptime(str(exp), EXPIRES_FMT).date()
    except ValueError:
        if warnings_out is not None:
            loc = source_path or ".roam/smells.suppress.yml"
            symbol = entry.get("symbol") or "<unset>"
            kind = entry.get("kind") or "<unset>"
            warnings_out.append(
                f"Edit {loc}: suppression entry symbol={symbol!r} "
                f"kind={kind!r} has unparseable expires={str(exp)!r}; "
                f"expected {EXPIRES_FMT!r}; treating as never-expires "
                f"until you replace the value with a valid date on one "
                f"of the suppression entries."
            )
        return False
    today_d = today or datetime.now(timezone.utc).date()
    return exp_date < today_d


def _symbol_matches(suppress_symbol: str, finding_symbol: str) -> bool:
    """Match a suppression symbol against a finding's bare symbol_name.

    The suppression entry may use a qualified name
    (``roam.languages.registry.get_language_for_file``) for human
    readability, while smell findings emit bare names
    (``get_language_for_file``). Accept either: exact match OR
    the suppression's trailing dot-segment equals the finding name.
    """
    if not suppress_symbol or not finding_symbol:
        return False
    if suppress_symbol == finding_symbol:
        return True
    tail = suppress_symbol.rsplit(".", 1)[-1]
    return tail == finding_symbol


def _file_matches(suppress_file: str | None, finding_location: str) -> bool:
    """Match an optional file suffix against the finding's location path.

    ``location`` is ``path:line`` or just ``path``. ``suppress_file`` is
    suffix-matched (forward-slash-normalised) so callers can write
    ``src/roam/languages/registry.py`` regardless of OS.
    """
    if not suppress_file:
        return True
    if not finding_location:
        return False
    file_part = finding_location.split(":", 1)[0]
    norm_loc = file_part.replace("\\", "/")
    norm_sup = suppress_file.replace("\\", "/")
    return norm_loc.endswith(norm_sup)


def is_suppressed(suppressions: list[dict], finding: dict, *, today: date | None = None) -> dict | None:
    """Return the matching suppression entry if *finding* is suppressed, else None.

    Returning the entry (rather than a bool) lets the caller surface the
    ``reason`` on the envelope for audit — mirroring the discipline in
    ``finding_suppress.annotate_with_suppression``.

    Matching is ``kind`` + ``symbol`` (+ optional ``file`` suffix), with
    expired entries skipped.
    """
    smell_id = finding.get("smell_id") or ""
    symbol_name = finding.get("symbol_name") or ""
    location = finding.get("location") or ""

    for entry in suppressions:
        if _is_expired(entry, today=today):
            continue
        if entry.get("kind") != smell_id:
            continue
        if not _symbol_matches(entry.get("symbol", ""), symbol_name):
            continue
        if not _file_matches(entry.get("file"), location):
            continue
        return entry

    return None


def apply_suppressions(
    findings: list[dict],
    suppressions: list[dict],
    *,
    today: date | None = None,
) -> tuple[list[dict], list[dict]]:
    """Partition *findings* into ``(kept, suppressed)`` by suppression match.

    The suppressed list carries each dropped finding annotated with a
    ``_suppressed_by`` key containing the matching suppression entry —
    callers that want an audit trail on the envelope can surface this
    rather than throwing the rows away silently.
    """
    if not suppressions:
        return list(findings), []

    kept: list[dict] = []
    suppressed: list[dict] = []
    for f in findings:
        entry = is_suppressed(suppressions, f, today=today)
        if entry is None:
            kept.append(f)
        else:
            annotated = dict(f)
            annotated["_suppressed_by"] = {
                "kind": entry.get("kind"),
                "symbol": entry.get("symbol"),
                "reason": entry.get("reason"),
                "expires": entry.get("expires"),
            }
            suppressed.append(annotated)
    return kept, suppressed


# ---------------------------------------------------------------------------
# Typed counterparts (W737 Phase C-1b of W692)
# ---------------------------------------------------------------------------
#
# These mirror the dict-shaped matcher + applier above against
# :class:`KindSymbolSuppression` instances. Same matching semantics, same
# output bytes — only the *input* type changes. The legacy dict surface
# stays in-tree for back-compat tests + any caller still on the dict path;
# the typed surface is the canonical entry point new code reaches for.


def is_suppressed_typed(
    suppressions: list[KindSymbolSuppression],
    finding: dict,
    *,
    today: date | None = None,
) -> KindSymbolSuppression | None:
    """Typed counterpart of :func:`is_suppressed` (W737 Phase C-1b).

    Return the matching :class:`KindSymbolSuppression` if *finding* is
    suppressed, else ``None``. Matching semantics are byte-identical to
    the dict-shaped applier:

    * ``kind`` (suppression) == ``smell_id`` (finding)
    * symbol match via :func:`_symbol_matches` (bare-name or qualified
      trailing-segment match)
    * optional ``file`` suffix match via :func:`_file_matches`
    * expired entries (``expires`` < today, UTC) skipped via the
      :meth:`KindSymbolSuppression.is_expired` dataclass method.
    """
    smell_id = finding.get("smell_id") or ""
    symbol_name = finding.get("symbol_name") or ""
    location = finding.get("location") or ""

    for entry in suppressions:
        if entry.is_expired(today=today):
            continue
        if entry.kind != smell_id:
            continue
        if not _symbol_matches(entry.symbol, symbol_name):
            continue
        if not _file_matches(entry.file, location):
            continue
        return entry

    return None


def apply_suppressions_typed(
    findings: list[dict],
    suppressions: list[KindSymbolSuppression],
    *,
    today: date | None = None,
) -> tuple[list[dict], list[dict]]:
    """Typed counterpart of :func:`apply_suppressions` (W737 Phase C-1b).

    Same matching semantics + same output bytes as the dict applier. The
    only change is the *input* type for *suppressions*: a list of
    :class:`KindSymbolSuppression` dataclasses rather than a list of raw
    dicts. The ``_suppressed_by`` annotation on each suppressed finding
    preserves the exact dict shape the dict applier emits (kind, symbol,
    reason, expires) — including the ISO-string serialisation of
    ``expires`` so the envelope bytes stay stable.
    """
    if not suppressions:
        return list(findings), []

    kept: list[dict] = []
    suppressed: list[dict] = []
    for f in findings:
        entry = is_suppressed_typed(suppressions, f, today=today)
        if entry is None:
            kept.append(f)
        else:
            annotated = dict(f)
            # Mirror the dict-applier annotation shape exactly. The
            # dict path emits the raw on-disk string for ``expires``;
            # the dataclass coerces it to :class:`date`. Project back
            # to the ISO string so envelope bytes stay byte-identical
            # to the dict-applier output. ``None`` is preserved as
            # ``None`` (matches dict applier behaviour when ``expires``
            # is absent from the on-disk entry).
            annotated["_suppressed_by"] = {
                "kind": entry.kind or None,
                "symbol": entry.symbol or None,
                "reason": entry.reason or None,
                "expires": entry.expires.isoformat() if entry.expires else None,
            }
            suppressed.append(annotated)
    return kept, suppressed
