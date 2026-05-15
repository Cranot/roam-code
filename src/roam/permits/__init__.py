"""Permit substrate — local-state persistence for ``roam permit issue``.

W198 closes the verdict-facade gap surfaced by the W186 audit: until this
wave, ``roam permit`` was a structural verdict facade (ALLOW / REVIEW /
BLOCK over a diff or symbol) but did NOT persist a stable ``permit_id``.
The W182 ``AuthorityRef(authority_kind="permit")`` slot in
:class:`roam.evidence.change_evidence.ChangeEvidence` was therefore
unfilled in production: the W268 ``_load_permits_from_disk`` reader
scanned ``.roam/permits/`` but no command wrote rows there.

This package adds the writer side. ``roam permit issue --persist`` (the
new W198 subcommand) writes one JSON document per issued permit, with
a stable ``permit_id`` of the form ``permit_<YYYYMMDD>_<6-hex>`` that
mirrors the W211 ``ApprovalRecord`` id format and the
``roam.leases.store`` ``lease_id`` shape so the three substrates feel
consistent at a glance.

Public surface
==============

* :class:`PermitRecord` — frozen dataclass mirroring the on-disk shape.
* :func:`issue_permit` — atomic-write a new permit; returns the record.
* :func:`read_permit` — load by id, or ``None`` if missing.
* :func:`list_permits` — enumerate every parseable permit in the repo.
* :func:`permits_root` — directory path helper (``.roam/permits``).
* :data:`PERMIT_ID_RE` — drift guard for the id format.

Discipline notes
================

* **Atomic writes only** — uses :func:`roam.atomic_io.atomic_write_json`
  so a crash mid-write can never leave a torn permit file on disk
  (Pattern 1 from the dogfood synthesis).
* **No body / no secrets** — the optional ``reason`` field is a single
  line of operator-supplied prose. Multi-line bodies are rejected at
  construction time (mirrors the W247a body-prohibition discipline).
* **Hash-stable JSON shape** — the on-disk schema matches what
  ``cmd_pr_bundle._load_permits_from_disk`` already reads, so a
  persisted permit flows through the W268 → W292 → W294 pipeline
  unchanged. The collector picks up ``permit_id`` and stamps it on the
  resulting ``AuthorityRef.extra["permit_id"]`` per W294.
"""

from __future__ import annotations

from roam.permits.store import (
    PERMIT_ID_RE,
    PermitRecord,
    issue_permit,
    list_permits,
    permits_root,
    read_permit,
)

__all__ = [
    "PERMIT_ID_RE",
    "PermitRecord",
    "issue_permit",
    "list_permits",
    "permits_root",
    "read_permit",
]
