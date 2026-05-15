"""W282 evidence-provenance helper.

Small vocabulary + helper wave per the strategic directive: a closed
enumeration (``PROVENANCE_SOURCES`` in :mod:`roam.evidence._vocabulary`)
plus a single compact API (:func:`provenance_label`) that future
producers can call to stamp a per-field provenance marker into the
free-form ``extra["provenance"]`` slot on ``ActorRef`` / ``AuthorityRef``
/ ``EnvironmentRef`` without a schema change.

Why distinct from ``CLAIM_CONFIDENCES`` (W210)?

* ``CLAIM_CONFIDENCES`` answers *how strongly do we trust this claim?*
  (``direct`` / ``derived`` / ``inferred`` / ``legacy_fallback``).
* ``PROVENANCE_SOURCES`` answers *where did the value come from?*
  (``ci_env_var`` / ``git_config`` / ``run_ledger`` / ...).

Both axes are needed: a ``"direct"`` confidence claim sourced from
``ci_env_var`` carries a different audit weight than the same
``"direct"`` confidence claim sourced from a ``cli_flag`` the agent set
itself.

This wave is vocabulary + helper ONLY. No producer wires this in yet;
the broader wiring sprint (W289+) will populate
``extra["provenance"] = provenance_label(...)`` at the actual surface
ingestion sites.

Determinism contract:

* Pure function. No env reads, no filesystem reads, no clock, no UUID,
  no globals.
* Same ``(source, detail)`` input -> same string output. Stable bytes
  for content-hash purposes if a future producer stamps the return
  value into a packet.
* Validates ``source`` against ``PROVENANCE_SOURCES`` and raises
  ``ValueError`` on unknown literals. Silently accepting unknown
  sources would defeat the closed-enumeration discipline.
"""

from __future__ import annotations

from roam.evidence._vocabulary import PROVENANCE_SOURCES


def provenance_label(source: str, *, detail: str | None = None) -> str:
    """Return a deterministic provenance-label string.

    Args:
        source: One of :data:`PROVENANCE_SOURCES`. Unknown values raise
            ``ValueError`` naming the rejected literal and the closed
            enumeration.
        detail: Optional sub-source detail (e.g. ``"user.email"`` when
            ``source="git_config"``, or ``"GITHUB_ACTOR"`` when
            ``source="ci_env_var"``). When provided, the return value
            uses the compact form ``"<source>(<detail>)"``; when
            omitted, the return value is the bare ``source`` literal.

    Returns:
        ``"<source>"`` when ``detail`` is ``None``, otherwise
        ``"<source>(<detail>)"``. The string format is stable and
        intended to be embedded in the free-form
        ``extra["provenance"]`` slot on agentic-assurance ref
        dataclasses.

    Raises:
        ValueError: ``source`` is not a member of
            :data:`PROVENANCE_SOURCES`.

    Examples:
        >>> provenance_label("git_config")
        'git_config'
        >>> provenance_label("git_config", detail="user.email")
        'git_config(user.email)'
        >>> provenance_label("ci_env_var", detail="GITHUB_ACTOR")
        'ci_env_var(GITHUB_ACTOR)'
    """
    if source not in PROVENANCE_SOURCES:
        raise ValueError(
            f"provenance_label: source={source!r} is not in "
            f"PROVENANCE_SOURCES"
        )
    if detail is None:
        return source
    return f"{source}({detail})"


__all__ = [
    "provenance_label",
]
