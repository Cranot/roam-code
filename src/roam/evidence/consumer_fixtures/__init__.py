"""Wheel-shipped golden consumer-contract fixtures for ``ChangeEvidence``.

This package is the published half of the evidence-envelope ecosystem
protocol. ``src/roam/evidence/change_evidence.py`` defines the packet
dataclass + the public parser (``ChangeEvidence.from_canonical_json``);
this directory ships a small, curated set of golden canonical-JSON
envelopes that pin the consumer contract: every shape a downstream
harness can be expected to ingest MUST round-trip byte-stably through
that parser and carry a self-consistent ``content_hash``.

Why a *published* fixture set (vs the schema-migration goldens in
``tests/fixtures/evidence/``). The ``tests/`` goldens pin the INTERNAL
hash-stability + schema-migration contract; they do not ship in the
wheel. A downstream consumer (a separate tool, a CI harness, an
attestation gateway) that depends on ``roam-code`` cannot reach those
files from a ``pip install``. The fixtures here ship as package data so
any consumer can load them at runtime via ``importlib.resources`` and
self-test its own ingestion of the protocol - turning the local
evidence format into something an ecosystem can rely on.

The three fixtures pin three distinct consumer-facing guarantees:

* ``consumer_minimal`` - the sparsest valid envelope (``evidence_id`` +
  ``schema_version`` only). A consumer MUST accept it; it correctly
  reports BELOW the minimum-viable-assurance floor (``passes == False``).
* ``consumer_assured`` - a fully-assured envelope exercising the whole
  field surface (actor / authority / environment refs, changed_subjects,
  findings, policy_decisions, tests_run, artifacts, redactions, version
  links). A consumer CAN rely on the complete surface; it passes the
  MVA floor and answers all eight evidence questions ``complete``.
* ``consumer_stale`` - the same full surface with ``evidence_stale`` set.
  A consumer reading staleness MUST downgrade trust independently of
  floor coverage (the W1254 additive axis: ``passes is True AND stale
  is True``).

Loader discipline. ``CONSUMER_FIXTURES`` is an explicit, ordered tuple
- the stable contract a consumer iterates. The companion compatibility
test ``tests/test_evidence_consumer_fixtures.py`` pins that the on-disk
``.json`` set matches this tuple exactly (drift guard) and that every
fixture round-trips through ``ChangeEvidence.from_canonical_json``.

Wheel packaging. The ``.json`` files ship via the
``"roam.evidence.consumer_fixtures" = ["*.json"]`` entry in
``pyproject.toml`` ``[tool.setuptools.package-data]`` - the same
discipline as ``roam.security.taint_rules`` (W610) and
``roam.templates.audit_report`` (W554). Without that line the package
code ships but the JSON does not, and every ``pip install`` consumer
silently loads zero fixtures (the W610 silent-empty class). This
``__init__.py`` exists (rather than the directory being a bare data
folder) so ``importlib.resources.files("roam.evidence.consumer_fixtures")``
returns a real on-disk path instead of a ``MultiplexedPath`` - the same
reason ``taint_rules`` carries an ``__init__.py``.

The loader resolves fixtures through ``importlib.resources.files(...)``
(not a ``Path(__file__).parent`` walk) so resolution survives a wheel
install where the package is unpacked under ``site-packages``.
"""

from __future__ import annotations

from collections.abc import Iterator
from importlib.resources import files

#: Stable, ordered registry of consumer-contract fixture basenames (no
#: ``.json`` suffix). A downstream harness iterates this tuple to load
#: every published envelope shape through the public parser. Adding a
#: new fixture is a two-step change: drop the ``<name>.json`` file in
#: this directory and append the basename here. The compatibility test's
#: dir-vs-registry drift guard keeps the two in sync.
CONSUMER_FIXTURES: tuple[str, ...] = (
    "consumer_minimal",
    "consumer_assured",
    "consumer_stale",
)


def load_consumer_fixture(name: str) -> str:
    """Return the canonical-JSON text for consumer fixture ``name``.

    Resolution goes through ``importlib.resources.files(...)`` so the
    same call works against a source checkout AND a ``pip install``
    wheel (the JSON ships under the unpacked package directory). Raises
    ``ValueError`` for an unknown name - the closed registry is the
    contract; typos surface immediately rather than resolving to an
    empty file.

    Args:
        name: A basename from :data:`CONSUMER_FIXTURES` (no ``.json``
            suffix).

    Returns:
        The canonical-JSON envelope text, suitable for
        ``ChangeEvidence.from_canonical_json``.
    """
    if name not in CONSUMER_FIXTURES:
        known = ", ".join(CONSUMER_FIXTURES)
        raise ValueError(f"Unknown consumer fixture {name!r}; known: {known}")
    resource = files("roam.evidence.consumer_fixtures") / f"{name}.json"
    return resource.read_text(encoding="utf-8")


def iter_consumer_fixtures() -> Iterator[tuple[str, str]]:
    """Yield ``(name, canonical_json_text)`` for each consumer fixture.

    Ordered by :data:`CONSUMER_FIXTURES` so a consumer iterating the
    registry sees a deterministic sequence.
    """
    for name in CONSUMER_FIXTURES:
        yield name, load_consumer_fixture(name)


__all__ = ["CONSUMER_FIXTURES", "iter_consumer_fixtures", "load_consumer_fixture"]
