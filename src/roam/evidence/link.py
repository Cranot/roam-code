"""``EvidenceLink`` - typed edge between two ``EvidenceSubject`` instances.

Inside the evidence packet a link answers: what claim is being made
about the relationship between source and target? See the docstring on
``LINK_KINDS`` in ``_vocabulary.py`` for the closed enumeration of
relation kinds.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any

from roam.evidence._vocabulary import LINK_KINDS
from roam.evidence.subject import EvidenceSubject


@dataclasses.dataclass(frozen=True)
class EvidenceLink:
    """One typed edge between two evidence subjects.

    Fields:

    * ``kind`` - one of ``LINK_KINDS``. Validated at construction.
    * ``source`` - the originating ``EvidenceSubject``.
    * ``target`` - the receiving ``EvidenceSubject``.
    * ``extra`` - structured detail (timestamps, attributes, ...).

    Links are directed and not symmetric: ``calls(a, b)`` says ``a``
    invokes ``b``, not the reverse. If both directions are meaningful,
    emit two links.
    """

    kind: str
    source: EvidenceSubject
    target: EvidenceSubject
    extra: Mapping[str, Any] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in LINK_KINDS:
            raise ValueError(f"EvidenceLink.kind={self.kind!r} is not in LINK_KINDS")
        if not isinstance(self.source, EvidenceSubject):
            raise ValueError("EvidenceLink.source must be an EvidenceSubject")
        if not isinstance(self.target, EvidenceSubject):
            raise ValueError("EvidenceLink.target must be an EvidenceSubject")


__all__ = ["EvidenceLink"]
