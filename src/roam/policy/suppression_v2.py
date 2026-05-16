"""W692 — Canonical Suppression dataclasses (discriminated-union match keys).

Four parsers across the codebase produce/consume suppression entries today:

* :mod:`roam.commands.suppression` — ``.roam-suppressions.yml`` (rule/file/line).
* :mod:`roam.commands.smells_suppress` — ``.roam/smells.suppress.yml`` (kind/symbol).
* :mod:`roam.commands.finding_suppress` — ``.roam/suppressions.json`` keyed by
  finding_id sha256 hash.
* :mod:`roam.output.sarif._load_suppressions` — projects the same
  ``.roam/suppressions.json`` for SARIF (ruleId/location) matching.

W691 unified the **on-disk schema** for ``.roam/suppressions.json`` so both
the dict-keyed and SARIF-projected readers consume the same writer's output.
W693 cross-checked that both readers stay in sync on synthetic files.

W692 closes the loop with a single canonical **in-memory** representation:
three frozen dataclasses that share a base of audit fields, discriminated by
the three match-key shapes already in use. The on-disk YAML/JSON formats stay
divergent (file-format consolidation is out of scope for W692); only the
runtime type unifies.

The discriminator is structural — the match-key field set is unique per
variant:

* ``RuleFileSuppression`` — ``{rule, file, line?}``. Used by
  ``.roam-suppressions.yml``.
* ``KindSymbolSuppression`` — ``{kind, symbol, file?}``. Used by
  ``.roam/smells.suppress.yml``.
* ``FindingIdSuppression`` — ``{finding_id}``. Used by ``roam suppress``
  and the SARIF projection.

Migration plan:

1. Phase A (this commit) — ship the module + parser-side ``from_dict``
   builders, and migrate ONE consumer (``commands.suppression``) to use it
   internally as a proof-of-concept. The legacy dict-shaped public API is
   preserved for back-compat.
2. Phase B (follow-up W) — migrate the other three consumers.
3. Phase C (follow-up W) — replace the legacy dict-shaped public APIs with
   the canonical types once every caller is on the new code.

This file is intentionally pure-Python (no third-party deps) so it can be
imported from any layer without dragging YAML or DB dependencies along.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Literal, Mapping, Optional, Union

# ---------------------------------------------------------------------------
# Status / source vocabularies (closed enumerations)
# ---------------------------------------------------------------------------

# Mirrors :data:`roam.commands.suppression.VALID_STATUSES` exactly.
SuppressionStatus = Literal["safe", "acknowledged", "wont-fix"]

# Closed enum of where a suppression came from. Lets consumers tell apart
# inline-annotation matches from on-disk-file matches without sniffing
# source strings.
SuppressionSource = Literal[
    "rule-file-yml",  # .roam-suppressions.yml entry
    "smells-suppress-yml",  # .roam/smells.suppress.yml entry
    "suppressions-json",  # .roam/suppressions.json entry (canonical W691 dict)
    "inline-annotation",  # roam: ignore-<command>[task_id] comment
    "ignore-findings-file",  # .roamignore-findings rule
]

VALID_STATUSES = frozenset({"safe", "acknowledged", "wont-fix"})


# ---------------------------------------------------------------------------
# Base class — universal audit fields
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _SuppressionBase:
    """Audit fields common to all suppression variants.

    Keep this minimal — only fields that every variant carries belong here.
    Variant-specific match keys live on the subclasses below.
    """

    reason: str = ""
    author: str = ""
    added: Optional[date] = None
    expires: Optional[date] = None
    status: Optional[SuppressionStatus] = None
    source: Optional[SuppressionSource] = None

    def is_expired(self, *, today: Optional[date] = None) -> bool:
        """Return True when ``expires`` is in the past.

        Mirrors the semantics of
        :func:`roam.commands.smells_suppress._is_expired` — missing/unparsed
        ``expires`` is treated as "never expires". UTC date comparison.
        """
        if self.expires is None:
            return False
        today_d = today or datetime.now(timezone.utc).date()
        return self.expires < today_d


# ---------------------------------------------------------------------------
# Discriminated-union variants
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleFileSuppression(_SuppressionBase):
    """Suppression keyed by (rule, file[, line]). ``.roam-suppressions.yml``.

    Maps directly onto the historical ``.roam-suppressions.yml`` row shape:
    ``{rule, file, line?, status, reason, author, date}``. The ``line`` field
    is optional; absence means "all lines in that file for that rule".
    """

    rule: str = ""
    file: str = ""
    line: Optional[int] = None

    @classmethod
    def from_dict(cls, entry: Mapping[str, Any]) -> "RuleFileSuppression":
        """Build from the legacy dict shape produced by
        :func:`roam.commands.suppression.load_suppressions`.

        Unknown keys are dropped silently — the legacy parser is lenient
        about extra fields and we preserve that.
        """
        return cls(
            rule=str(entry.get("rule", "")),
            file=str(entry.get("file", "")).replace("\\", "/"),
            line=_coerce_int(entry.get("line")),
            reason=str(entry.get("reason", "")),
            author=str(entry.get("author", "")),
            added=_coerce_date(entry.get("date") or entry.get("added")),
            expires=_coerce_date(entry.get("expires")),
            status=_coerce_status(entry.get("status")),
            source="rule-file-yml",
        )

    def to_dict(self) -> dict[str, Any]:
        """Project back to the legacy dict shape (round-trippable).

        Field order matches the historical serialiser in
        :func:`roam.commands.suppression._serialize_suppressions` so a
        round-trip through ``from_dict`` -> ``to_dict`` stays stable.
        """
        out: dict[str, Any] = {"rule": self.rule, "file": self.file}
        if self.line is not None:
            out["line"] = self.line
        if self.reason:
            out["reason"] = self.reason
        if self.status:
            out["status"] = self.status
        if self.author:
            out["author"] = self.author
        if self.added is not None:
            out["date"] = self.added.isoformat()
        if self.expires is not None:
            out["expires"] = self.expires.isoformat()
        return out


@dataclass(frozen=True)
class KindSymbolSuppression(_SuppressionBase):
    """Suppression keyed by (kind, symbol[, file]). ``.roam/smells.suppress.yml``.

    The smells substrate disambiguates by *symbol* rather than (rule, file)
    because shotgun-surgery on a public-API hub is intended, while the same
    smell on a one-off helper is real. See
    :mod:`roam.commands.smells_suppress` for the full rationale.
    """

    kind: str = ""
    symbol: str = ""
    file: Optional[str] = None

    @classmethod
    def from_dict(cls, entry: Mapping[str, Any]) -> "KindSymbolSuppression":
        """Build from the legacy dict shape produced by
        :func:`roam.commands.smells_suppress.load_smells_suppressions`.
        """
        file_val = entry.get("file")
        return cls(
            kind=str(entry.get("kind", "")),
            symbol=str(entry.get("symbol", "")),
            file=str(file_val).replace("\\", "/") if file_val else None,
            reason=str(entry.get("reason", "")),
            author=str(entry.get("author", "")),
            added=_coerce_date(entry.get("added") or entry.get("date")),
            expires=_coerce_date(entry.get("expires")),
            status=_coerce_status(entry.get("status")),
            source="smells-suppress-yml",
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind, "symbol": self.symbol}
        if self.file:
            out["file"] = self.file
        if self.reason:
            out["reason"] = self.reason
        if self.status:
            out["status"] = self.status
        if self.author:
            out["author"] = self.author
        if self.added is not None:
            out["added"] = self.added.isoformat()
        if self.expires is not None:
            out["expires"] = self.expires.isoformat()
        return out


@dataclass(frozen=True)
class FindingIdSuppression(_SuppressionBase):
    """Suppression keyed by finding_id sha256. ``.roam/suppressions.json``.

    The finding_id is the deterministic short-hash from
    :func:`roam.commands.finding_suppress.finding_id` —
    ``sha256(task_id|location|symbol_name)[:16]``. This is a one-way hash;
    the optional ``rule_id`` / ``location`` fields are the SARIF projection
    that lets the SARIF loader bind hash-keyed entries back to (ruleId,
    location) tuples without reversing the hash.
    """

    finding_id: str = ""
    # SARIF projection fields — optional; entries without them are only
    # visible to the dict-keyed finding_suppress reader (not SARIF).
    rule_id: Optional[str] = None
    location: Optional[str] = None
    task_id: Optional[str] = None
    symbol_name: Optional[str] = None

    @classmethod
    def from_dict(cls, finding_id_key: str, entry: Mapping[str, Any]) -> "FindingIdSuppression":
        """Build from a ``.roam/suppressions.json`` entry.

        ``finding_id_key`` is the dict key (the hash); ``entry`` is the
        value dict that holds reason/added_at/source/rule_id/location etc.
        """
        return cls(
            finding_id=str(finding_id_key),
            rule_id=_str_or_none(entry.get("rule_id") or entry.get("ruleId")),
            location=_str_or_none(entry.get("location")),
            task_id=_str_or_none(entry.get("task_id")),
            symbol_name=_str_or_none(entry.get("symbol_name")),
            reason=str(entry.get("reason", "")),
            author=str(entry.get("author", "")),
            added=_coerce_date(entry.get("added_at") or entry.get("added") or entry.get("date")),
            expires=_coerce_date(entry.get("expires")),
            status=_coerce_status(entry.get("status")),
            source="suppressions-json",
        )

    def to_dict(self) -> dict[str, Any]:
        """Project back to ``.roam/suppressions.json`` entry shape.

        Returns the *value* dict only; the caller supplies the
        ``finding_id`` as the dict key when assembling the full file.
        """
        out: dict[str, Any] = {}
        if self.reason:
            out["reason"] = self.reason
        if self.added is not None:
            out["added_at"] = self.added.isoformat()
        if self.rule_id:
            out["rule_id"] = self.rule_id
        if self.location:
            out["location"] = self.location
        if self.task_id:
            out["task_id"] = self.task_id
        if self.symbol_name:
            out["symbol_name"] = self.symbol_name
        if self.author:
            out["author"] = self.author
        if self.status:
            out["status"] = self.status
        if self.expires is not None:
            out["expires"] = self.expires.isoformat()
        return out


# Public discriminated-union alias — type-checker-friendly.
Suppression = Union[RuleFileSuppression, KindSymbolSuppression, FindingIdSuppression]


# ---------------------------------------------------------------------------
# Coercion helpers (defensive — every parser is lenient about types)
# ---------------------------------------------------------------------------


def _coerce_int(value: Any) -> Optional[int]:
    """Best-effort integer coercion; returns None on failure."""
    if value is None or value == "":
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _coerce_date(value: Any) -> Optional[date]:
    """Accept ISO ``YYYY-MM-DD`` strings or :class:`date` objects.

    Lenient — returns None on any parse failure so a malformed entry
    never crashes the loader (mirrors the existing parser discipline).
    """
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        text = str(value).strip()
        # Accept ISO-8601 with or without time component.
        if "T" in text:
            text = text.split("T", 1)[0]
        return datetime.strptime(text, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _coerce_status(value: Any) -> Optional[SuppressionStatus]:
    """Validate against the closed status enumeration; None if unknown."""
    if value is None:
        return None
    text = str(value).strip()
    if text in VALID_STATUSES:
        return text  # type: ignore[return-value]
    return None


def _str_or_none(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    return str(value)


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------

__all__ = [
    "FindingIdSuppression",
    "KindSymbolSuppression",
    "RuleFileSuppression",
    "Suppression",
    "SuppressionSource",
    "SuppressionStatus",
    "VALID_STATUSES",
]
