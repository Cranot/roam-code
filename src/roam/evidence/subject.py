"""``EvidenceSubject`` - portable identity wrapper around the things
Roam already sees (symbols, files, endpoints, runs, controls, ...).

Why this exists: ``symbols.id`` is a local SQLite identity that does not
survive reindex or move to another machine. Evidence needs a portable
identifier that can appear in reports, SARIF, attestations, and the
eventual cloud projection. ``EvidenceSubject`` is that wrapper.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any

from roam.evidence._vocabulary import SUBJECT_KINDS


@dataclasses.dataclass(frozen=True)
class EvidenceSubject:
    """Stable identity for one thing an evidence packet refers to.

    Fields:

    * ``kind`` - one of ``SUBJECT_KINDS``. Validated at construction;
      passing an unknown kind raises ``ValueError``.
    * ``qualified_name`` - portable identifier string. Convention is
      ``"<context>::<name>"`` for symbol-like subjects (e.g.
      ``"src/foo.py::bar"``) and ``"<scheme>:<value>"`` for protocol-
      like ones (e.g. ``"endpoint:POST /api/users"``,
      ``"commit:7f3a9c1"``). Consumers must not parse the inside; the
      ``extra`` mapping is the place for structured detail.
    * ``repo_id`` - optional repository identity. ``None`` means
      "the current repo"; populate for cross-repo evidence.
    * ``extra`` - free-form structured detail. Kept tiny (<1 KB on
      disk) because it serialises into the packet content hash.

    The dataclass is frozen so subjects are usable as dict keys, set
    members, and tuple elements without surprise mutation. ``extra`` is
    typed as ``Mapping`` (read-only protocol) but callers should pass a
    dict literal — dataclasses' default factory builds a fresh dict per
    instance, which is the safest behaviour.
    """

    kind: str
    qualified_name: str
    repo_id: str | None = None
    extra: Mapping[str, Any] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in SUBJECT_KINDS:
            raise ValueError(
                f"EvidenceSubject.kind={self.kind!r} is not in SUBJECT_KINDS"
            )
        if not isinstance(self.qualified_name, str) or not self.qualified_name:
            raise ValueError(
                "EvidenceSubject.qualified_name must be a non-empty string"
            )


__all__ = ["EvidenceSubject"]
