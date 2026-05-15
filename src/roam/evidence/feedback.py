"""``FindingFeedback`` — reviewer accept/dismiss with rationale (W228).

The false-positive feedback loop. The directive (W228):

    If a finding is accepted or dismissed, capture why. That becomes
    rules-pack improvement data.

The contract is intentionally narrow: a reviewer's explicit decision
on a finding, with a short classifier-friendly rationale tag. The
rationale is the rules-pack improvement primitive — when 90% of
dismissals on the "feature-envy on tree-sitter visitors" smell cite
``"visitor-pattern"``, the rule should learn to skip visitor classes.

Design mirrors :mod:`roam.evidence.approval` and
:mod:`roam.evidence.mcp_receipt`:

* Frozen dataclass so a feedback record can be hashed / used inside
  content-hashed evidence packets.
* Closed-enumeration validation on ``decision``.
* Free-text optional and truncated to 500 chars to keep payloads small
  and discourage prose-as-classifier.
* ``persist_feedback`` mirrors the W196 receipt discipline: best-effort
  filesystem write that NEVER breaks the caller. A swallowed write
  failure is acceptable — the substrate is improvement data, not the
  source of truth.

NON-GOALS
=========

* No reviewer credentials. ``reviewer`` is an actor identity string
  (typically :attr:`roam.evidence.refs.ActorRef.actor_id`), never a
  token / password / signing key.
* No raw comment bodies. If a reviewer wrote prose, prefer storing it
  in an audit-trail row and referencing it from ``extra`` by id. The
  optional ``free_text`` field is a 500-char escape hatch for short
  notes only — emphatically NOT a chat log.
* No PII. ``reviewer`` is the actor identity string; nothing personal
  beyond that should land in this dataclass.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional, Union

from roam.atomic_io import atomic_write_json

# ---------------------------------------------------------------------------
# Closed-enumeration vocabulary
# ---------------------------------------------------------------------------

#: Closed enumeration of reviewer decisions on a finding.
#:
#: * ``accepted_real``            — finding is real; reviewer is accepting
#:                                  it / will fix
#: * ``dismissed_false_positive`` — finding is wrong
#: * ``dismissed_by_design``      — finding is correct but the code is
#:                                  intentional
#: * ``dismissed_test_fixture``   — finding is on test fixture / synthetic
#:                                  data
#: * ``deferred``                 — acknowledged but pushed to later
#: * ``duplicate``                — same as another finding
FEEDBACK_DECISIONS: frozenset[str] = frozenset({
    "accepted_real",
    "dismissed_false_positive",
    "dismissed_by_design",
    "dismissed_test_fixture",
    "deferred",
    "duplicate",
})

#: Set of decisions that count as a dismissal. Used by
#: :func:`aggregate_dismissal_reasons` to filter the rationale stream
#: down to the rules-pack improvement signal.
_DISMISSAL_DECISIONS: frozenset[str] = frozenset({
    "dismissed_false_positive",
    "dismissed_by_design",
    "dismissed_test_fixture",
})

#: Maximum length for the optional ``free_text`` field. Anything longer
#: is truncated at construction time. The cap keeps feedback payloads
#: small (so they round-trip cleanly through evidence packets) and
#: discourages prose-as-classifier — if you need to write more, file an
#: audit-trail row and reference it from ``extra``.
FREE_TEXT_MAX_CHARS: int = 500

#: Default on-disk location for feedback JSON files (repo-local).
DEFAULT_FEEDBACK_DIR: str = ".roam/feedback"


# ---------------------------------------------------------------------------
# Filename sanitisation
# ---------------------------------------------------------------------------

# Whitelist of safe filename chars: alnum, dash, underscore. We
# deliberately EXCLUDE ``.`` from the whitelist — that closes the
# path-traversal door at sanitiser level rather than relying on the FS
# (no ``..``, ``...``, or hidden-file stems can ever be produced).
# ``finding_id_str`` convention is ``"<detector>:<subject>:<hash>"`` so we
# also need to translate the colon delimiter into something filesystem-
# friendly. Anything outside the whitelist becomes an underscore.
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9_-]")

# Hard cap on the sanitised stem length. Some filesystems still cap path
# components at 255 bytes; leaving ~80 chars for the timestamp + suffix
# keeps us safely under that.
_MAX_STEM_LEN: int = 160


def _sanitize_for_filename(value: str) -> str:
    """Translate an arbitrary string into a path-traversal-safe stem.

    Rules (opinionated, documented for reviewers):

    * Replace any char outside ``[A-Za-z0-9_-]`` with ``_``. This
      collapses ``/``, ``\\``, ``:``, ``.``, NUL, and every other
      separator onto a single safe sentinel. We deliberately treat
      ``.`` as unsafe (despite it being a valid filename byte) so that
      no produced stem can contain ``..``, ``...`` or
      ``./hidden_file`` — closing the path-traversal door at the
      filename level rather than at the FS layer.
    * Collapse runs of underscores so the result stays readable.
    * Cap at :data:`_MAX_STEM_LEN` bytes so the full path stays under
      the 255-byte component limit on common filesystems.
    * Empty / all-unsafe input maps to ``"unknown"`` rather than ``""``
      so we always produce a non-empty filename.

    The transform is intentionally lossy. The full unsanitised
    ``finding_id_str`` is preserved verbatim inside the JSON payload —
    the filename is just an index hint, not the authoritative id.
    """
    if not value:
        return "unknown"
    # Replace unsafe chars with underscore. Note: ``.`` is excluded
    # from the whitelist on purpose (see docstring rationale).
    safe = _SAFE_FILENAME_RE.sub("_", value)
    # Collapse runs of underscores (cosmetic; keeps filenames readable).
    safe = re.sub(r"_+", "_", safe)
    # Strip leading/trailing underscores so the stem reads cleanly.
    safe = safe.strip("_")
    if not safe:
        return "unknown"
    if len(safe) > _MAX_STEM_LEN:
        safe = safe[:_MAX_STEM_LEN]
    return safe


def _timestamp_for_filename(ts: str) -> str:
    """Translate an ISO-8601 timestamp into a filename-safe token.

    Colons (the ISO standard time-of-day delimiter) and ``+`` (TZ
    offsets) are unsafe on Windows. Same sanitiser as
    :func:`_sanitize_for_filename` but we keep it as its own helper so
    the intent at the call site stays readable.
    """
    return _sanitize_for_filename(ts)


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class FindingFeedback:
    """A reviewer's explicit acceptance/dismissal of a finding, with reason.

    These feed the rules-pack improvement loop: if 90% of dismissals on
    the "feature-envy on tree-sitter visitors" smell finding cite the
    same rationale, the rule should learn to skip visitor classes.

    Fields:

    * ``finding_id_str`` — links to the central findings registry (see
      :class:`roam.db.findings.FindingRecord`). Stored verbatim;
      consumers JOIN against ``findings.finding_id_str``.
    * ``decision`` — one of :data:`FEEDBACK_DECISIONS`. Validated at
      construction time.
    * ``rationale`` — short classifier-friendly tag. Examples:
      ``"visitor-pattern"``, ``"test-fixture"``, ``"intentional"``,
      ``"third-party-code"``, ``"performance-tradeoff"``. Convention:
      kebab-case, no spaces. This is the field
      :func:`aggregate_dismissal_reasons` groups by.
    * ``reviewer`` — actor identity string (e.g.
      ``"human:alice@example.com"``,
      ``"ci_runner:github.com/owner/repo/runs/123"``). Convention
      matches :attr:`roam.evidence.refs.ActorRef.actor_id`. Stored
      verbatim; consumers do not parse the inside.
    * ``timestamp`` — ISO-8601 UTC timestamp of when the feedback was
      recorded.
    * ``free_text`` — optional longer note. Truncated at construction
      time to :data:`FREE_TEXT_MAX_CHARS` chars.
    * ``extra`` — free-form structured detail (PR number, ticket id,
      audit-trail row id, ...). Kept tiny since it round-trips through
      content-hashed evidence packets.

    NON-GOAL: this dataclass does not store reviewer credentials, raw
    comment bodies, or PII. The ``rationale`` field is intended as
    short, classifiable text.
    """

    finding_id_str: str
    decision: str
    rationale: str
    reviewer: str
    timestamp: str
    free_text: Optional[str] = None
    extra: Mapping[str, Any] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.finding_id_str, str) or not self.finding_id_str:
            raise ValueError(
                "FindingFeedback.finding_id_str must be a non-empty string"
            )
        if self.decision not in FEEDBACK_DECISIONS:
            raise ValueError(
                f"unknown decision: {self.decision!r}; "
                f"expected one of {sorted(FEEDBACK_DECISIONS)}"
            )
        if not isinstance(self.rationale, str) or not self.rationale:
            raise ValueError(
                "FindingFeedback.rationale must be a non-empty string"
            )
        if not isinstance(self.reviewer, str) or not self.reviewer:
            raise ValueError(
                "FindingFeedback.reviewer must be a non-empty string"
            )
        if not isinstance(self.timestamp, str) or not self.timestamp:
            raise ValueError(
                "FindingFeedback.timestamp must be a non-empty ISO-8601 "
                "UTC string"
            )
        # Validate timestamp parses (fail fast on garbage); we do not
        # store the parsed datetime because the dataclass is frozen and
        # the canonical-JSON form must round-trip the original string.
        try:
            _parse_iso(self.timestamp)
        except ValueError as exc:
            raise ValueError(
                f"FindingFeedback.timestamp is not ISO-8601 parseable: "
                f"{self.timestamp!r} ({exc})"
            ) from exc
        # free_text: truncate (NOT reject) — UX choice. Reviewers paste
        # in long-ish notes; silently truncating is friendlier than
        # raising. We MUST use object.__setattr__ because the dataclass
        # is frozen.
        if self.free_text is not None:
            if not isinstance(self.free_text, str):
                raise ValueError(
                    "FindingFeedback.free_text must be a string or None"
                )
            if len(self.free_text) > FREE_TEXT_MAX_CHARS:
                object.__setattr__(
                    self, "free_text", self.free_text[:FREE_TEXT_MAX_CHARS]
                )

    def to_canonical_json(self) -> str:
        """Deterministic JSON: sorted keys, no insignificant whitespace.

        Same conventions as :meth:`McpDecisionReceipt.to_canonical_json`
        so a feedback record's payload is reproducible across processes
        and Python versions. Used by callers that want to hash the
        record before persisting.
        """
        obj = dataclasses.asdict(self)
        return json.dumps(obj, sort_keys=True, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


PathLike = Union[str, Path]


def persist_feedback(
    feedback: FindingFeedback,
    *,
    feedback_dir: PathLike = DEFAULT_FEEDBACK_DIR,
) -> str:
    """Atomic write to ``<feedback_dir>/<sanitised_id>__<timestamp>.json``.

    Mirrors the W196 receipt discipline: best-effort. Write failures
    NEVER break the caller — they are swallowed and an empty string is
    returned to signal "not persisted". Rationale: feedback is
    improvement data, not the source of truth. A read-only volume / FS
    quota / permission glitch should never prevent the upstream review
    workflow from completing.

    Returns the absolute path to the written file on success, or the
    empty string when the write was swallowed. Callers that need to
    fail loud on persistence errors should call
    :func:`atomic_write_json` directly.
    """
    target_dir = Path(feedback_dir)
    stem = _sanitize_for_filename(feedback.finding_id_str)
    ts = _timestamp_for_filename(feedback.timestamp)
    filename = f"{stem}__{ts}.json"
    target = target_dir / filename
    payload = dict(dataclasses.asdict(feedback))
    try:
        atomic_write_json(target, payload, indent=2, sort_keys=True)
    except (OSError, PermissionError, ValueError, TypeError):
        # Best-effort: read-only dir / quota / etc. should never break
        # the caller. Pattern matches `cmd_pr_bundle` and the W196
        # receipt emitter in `mcp_server.py`.
        return ""
    return str(target.resolve())


def load_feedback(
    finding_id_str: Optional[str] = None,
    *,
    feedback_dir: PathLike = DEFAULT_FEEDBACK_DIR,
) -> tuple[FindingFeedback, ...]:
    """Load all feedback (optionally filtered by ``finding_id_str``).

    Skip-and-warn on malformed JSON or schema mismatch — a single bad
    file should never prevent the rest from loading. The warning is
    emitted via :mod:`warnings` (UserWarning) so test harnesses with
    ``-W error`` can opt into strict behaviour.

    Returns the matching records in stable order (sorted by timestamp
    then filename so callers can iterate deterministically).
    """
    import warnings

    target_dir = Path(feedback_dir)
    if not target_dir.is_dir():
        return ()

    records: list[FindingFeedback] = []
    # Sort filenames for deterministic iteration order (file listings
    # are platform-dependent without an explicit sort).
    for entry in sorted(target_dir.iterdir()):
        if not entry.is_file() or entry.suffix != ".json":
            continue
        try:
            raw = entry.read_text(encoding="utf-8")
            obj = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            warnings.warn(
                f"load_feedback: skipping malformed feedback file "
                f"{entry.name!r}: {exc}",
                UserWarning,
                stacklevel=2,
            )
            continue
        if not isinstance(obj, dict):
            warnings.warn(
                f"load_feedback: skipping non-object feedback file "
                f"{entry.name!r}",
                UserWarning,
                stacklevel=2,
            )
            continue
        try:
            record = FindingFeedback(
                finding_id_str=obj["finding_id_str"],
                decision=obj["decision"],
                rationale=obj["rationale"],
                reviewer=obj["reviewer"],
                timestamp=obj["timestamp"],
                free_text=obj.get("free_text"),
                extra=obj.get("extra") or {},
            )
        except (KeyError, TypeError, ValueError) as exc:
            warnings.warn(
                f"load_feedback: skipping invalid feedback file "
                f"{entry.name!r}: {exc}",
                UserWarning,
                stacklevel=2,
            )
            continue
        if finding_id_str is not None and record.finding_id_str != finding_id_str:
            continue
        records.append(record)

    # Stable sort: timestamp first, then finding_id_str — keeps the
    # output deterministic when two records share a timestamp.
    records.sort(key=lambda r: (r.timestamp, r.finding_id_str))
    return tuple(records)


def aggregate_dismissal_reasons(
    *,
    detector: str,
    feedback_dir: PathLike = DEFAULT_FEEDBACK_DIR,
) -> dict[str, int]:
    """For a given detector, count rationales across all dismissals.

    Returns ``{rationale: count}`` ordered by descending count, ties
    broken alphabetically. The detector match is the prefix of the
    ``finding_id_str`` (convention from
    :class:`roam.db.findings.FindingRecord`:
    ``"<detector>:<subject>:<hash>"``).

    This is the rules-pack improvement-data primitive: a rule whose
    top rationale is ``"visitor-pattern"`` should learn to skip
    visitor classes. Empty mapping if no dismissals match the
    detector.
    """
    if not isinstance(detector, str) or not detector:
        raise ValueError(
            "aggregate_dismissal_reasons: detector must be a non-empty string"
        )

    all_feedback = load_feedback(feedback_dir=feedback_dir)
    prefix = f"{detector}:"
    counts: dict[str, int] = {}
    for rec in all_feedback:
        if rec.decision not in _DISMISSAL_DECISIONS:
            continue
        if not rec.finding_id_str.startswith(prefix):
            continue
        counts[rec.rationale] = counts.get(rec.rationale, 0) + 1

    # Return ordered dict: descending count, alphabetical tie-break.
    # Python 3.7+ dicts preserve insertion order — relied on by
    # callers that want the "top rationale" to be the first key.
    return dict(
        sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_iso(value: str) -> _dt.datetime:
    """Parse an ISO-8601 string into a tz-aware UTC ``datetime``.

    Same contract as :func:`roam.evidence.approval._parse_iso`: accepts
    the trailing-``Z`` form and explicit offsets; treats naive
    timestamps as UTC. Centralising would create a cross-module import
    just for one helper, so we keep a tiny local copy.
    """
    normalised = value
    if normalised.endswith("Z"):
        normalised = normalised[:-1] + "+00:00"
    parsed = _dt.datetime.fromisoformat(normalised)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed


__all__ = [
    "DEFAULT_FEEDBACK_DIR",
    "FEEDBACK_DECISIONS",
    "FREE_TEXT_MAX_CHARS",
    "FindingFeedback",
    "aggregate_dismissal_reasons",
    "load_feedback",
    "persist_feedback",
]
