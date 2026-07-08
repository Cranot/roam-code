"""Consumer-contract compatibility test for ``ChangeEvidence`` envelopes.

This is the published-contract companion to the wheel-shipped golden
fixtures in ``src/roam/evidence/consumer_fixtures/``. Where
``test_evidence_schema_migration.py`` pins the INTERNAL schema-migration
hash-stability contract against on-disk goldens under ``tests/`` (which
do not ship in the wheel), THIS test pins the PUBLISHED consumer
contract: every envelope a downstream harness is expected to ingest
MUST load through the public parser
(:meth:`ChangeEvidence.from_canonical_json`), round-trip byte-stably,
and carry a self-consistent ``content_hash``.

The ecosystem-protocol thesis. ``ChangeEvidence`` is no longer a local
implementation detail - it is a wire format other tools consume. A
downstream consumer (a CI harness, an attestation gateway, a separate
roam plugin) that depends on ``roam-code`` reaches these fixtures at
runtime via ``importlib.resources`` and uses them to self-test its own
ingestion. If the published envelopes drift away from what the public
parser accepts, or lose their self-consistent hash, every such consumer
breaks silently. The parametrised checks below are the gate that turns
that silent break into a loud test failure.

The contract pinned per fixture (parametrised over
:data:`CONSUMER_FIXTURES`):

* **Parse.** The fixture deserialises through
  :meth:`ChangeEvidence.from_canonical_json` without error.
* **Round-trip.** ``packet.to_canonical_json()`` is byte-identical to
  the fixture text (no whitespace / key-order drift through the public
  parser).
* **Self-consistent hash.** The envelope's stamped ``content_hash``
  equals ``packet.compute_content_hash()`` - i.e. the consumer-verifiable
  hash is valid (the value a gateway recomputes to detect tampering).
* **Schema stamp.** ``packet.schema_version == EVIDENCE_SCHEMA_VERSION``.

Per-fixture consumer guarantees (one focused test each):

* ``consumer_minimal``  - sparse envelope correctly reports BELOW the
  minimum-viable-assurance floor (``passes is False``).
* ``consumer_assured``  - full-surface envelope PASSES the floor and
  answers all eight evidence questions ``complete``.
* ``consumer_stale``    - full-surface-but-stale envelope PASSES the
  floor yet reports ``stale`` (the W1254 additive trust axis).

Drift guards:

* Every fixture is reachable via ``importlib.resources`` (the wheel-safe
  path; fails if the ``pyproject.toml`` package-data line is dropped).
* The on-disk ``.json`` set matches :data:`CONSUMER_FIXTURES` exactly
  (catches a fixture added to the directory but not the registry, or
  vice versa).
"""

from __future__ import annotations

from importlib.resources import files

import pytest

from roam.evidence import EVIDENCE_SCHEMA_VERSION, ChangeEvidence
from roam.evidence.consumer_fixtures import (
    CONSUMER_FIXTURES,
    iter_consumer_fixtures,
    load_consumer_fixture,
)


def test_consumer_fixtures_registry_non_empty() -> None:
    """The published contract must list at least one fixture."""
    assert CONSUMER_FIXTURES, "CONSUMER_FIXTURES registry is empty - no published consumer contract to pin."


@pytest.mark.parametrize("name", CONSUMER_FIXTURES)
def test_consumer_fixture_parses_via_public_parser(name: str) -> None:
    """Each fixture loads through the public parser and round-trips.

    The public parser downstream harnesses use is
    :meth:`ChangeEvidence.from_canonical_json`. The fixture's on-disk
    bytes MUST be byte-identical to ``to_canonical_json()`` of the
    reconstructed packet, and the stamped ``content_hash`` MUST equal
    ``compute_content_hash()`` (the consumer-verifiable tamper check).
    """
    text = load_consumer_fixture(name)
    packet = ChangeEvidence.from_canonical_json(text)

    # Round-trip: public parser then serialiser reproduces the bytes.
    assert packet.to_canonical_json() == text, (
        f"consumer fixture {name!r} does not round-trip byte-stably "
        f"through ChangeEvidence.from_canonical_json -> to_canonical_json"
    )
    # Self-consistent, consumer-verifiable content hash.
    assert packet.content_hash == packet.compute_content_hash(), (
        f"consumer fixture {name!r} stamped content_hash does not match "
        f"compute_content_hash() - the consumer tamper-check would fail"
    )
    # Schema-version stamp agrees with the current constant.
    assert packet.schema_version == EVIDENCE_SCHEMA_VERSION, (
        f"consumer fixture {name!r} schema_version "
        f"{packet.schema_version!r} != EVIDENCE_SCHEMA_VERSION "
        f"{EVIDENCE_SCHEMA_VERSION!r}"
    )


@pytest.mark.parametrize("name", CONSUMER_FIXTURES)
def test_consumer_fixture_reachable_via_importlib_resources(name: str) -> None:
    """Each fixture resolves through ``importlib.resources`` (wheel-safe).

    This is the exact resolution path a downstream harness uses after a
    ``pip install roam-code``. It fails the moment the
    ``"roam.evidence.consumer_fixtures" = ["*.json"]`` package-data line
    is dropped from ``pyproject.toml`` (the W610 silent-empty class) -
    on a source checkout the file still resolves, but under a wheel
    install ``is_file()`` returns ``False``.
    """
    resource = files("roam.evidence.consumer_fixtures") / f"{name}.json"
    assert resource.is_file(), (
        f"{name}.json is not reachable via "
        f"importlib.resources.files('roam.evidence.consumer_fixtures'). "
        f"Check pyproject.toml [tool.setuptools.package-data] still "
        f'includes "roam.evidence.consumer_fixtures" = ["*.json"].'
    )


def test_consumer_fixtures_dir_matches_registry() -> None:
    """Every ``.json`` on disk is listed in :data:`CONSUMER_FIXTURES`.

    Drift guard: a fixture file added to the directory but not appended
    to the registry (or the reverse) would either silently ship an
    untested envelope or leave a registry entry pointing at nothing.
    """
    pkg_dir = files("roam.evidence.consumer_fixtures")
    on_disk = sorted(p.name for p in pkg_dir.iterdir() if str(p.name).endswith(".json"))
    listed = sorted(f"{n}.json" for n in CONSUMER_FIXTURES)
    assert on_disk == listed, f"consumer_fixtures directory / registry drift: on_disk={on_disk} registry={listed}"


def test_iter_consumer_fixtures_covers_registry() -> None:
    """``iter_consumer_fixtures`` yields exactly the registry, in order."""
    pairs = list(iter_consumer_fixtures())
    names = [name for name, _ in pairs]
    assert names == list(CONSUMER_FIXTURES), f"iter_consumer_fixtures order drift: {names} != {list(CONSUMER_FIXTURES)}"
    # Every yielded text must itself parse (defence in depth).
    for name, text in pairs:
        ChangeEvidence.from_canonical_json(text)


def test_load_consumer_fixture_rejects_unknown_name() -> None:
    """Unknown fixture names raise ``ValueError`` (closed registry)."""
    with pytest.raises(ValueError, match="Unknown consumer fixture"):
        load_consumer_fixture("does_not_exist")


# ---------------------------------------------------------------------------
# Per-fixture consumer guarantees
# ---------------------------------------------------------------------------


def test_consumer_minimal_reports_below_floor() -> None:
    """The sparsest valid envelope correctly reports BELOW the MVA floor.

    A consumer that gates on ``assurance_floor()['passes']`` MUST see a
    sparse envelope as below-floor - it carries no actor / authority /
    findings / verification. This pins that the floor does not silently
    pass an empty packet.
    """
    packet = ChangeEvidence.from_canonical_json(load_consumer_fixture("consumer_minimal"))
    floor = packet.assurance_floor()
    assert floor["passes"] is False
    assert floor["stale"] is False
    assert "actor" in floor["missing"]
    assert "authority" in floor["missing"]
    assert "findings" in floor["missing"]
    assert "verification" in floor["missing"]


def test_consumer_assured_passes_floor_and_complete() -> None:
    """The full-surface envelope passes the floor and is fully complete.

    Pins the positive consumer contract: a producer that populates
    every field produces an envelope a consumer can attest to -
    ``passes is True`` and all eight evidence questions answer
    ``complete``.
    """
    packet = ChangeEvidence.from_canonical_json(load_consumer_fixture("consumer_assured"))
    floor = packet.assurance_floor()
    assert floor["passes"] is True
    assert floor["stale"] is False
    assert floor["missing"] == ()
    completeness = packet.evidence_completeness()
    assert completeness["complete"] == 8, (
        f"expected all 8 evidence questions complete, got "
        f"complete={completeness['complete']} partial={completeness['partial']} "
        f"missing={completeness['missing']}"
    )
    assert completeness["stale"] is False


def test_consumer_stale_reports_stale_but_passes_floor() -> None:
    """A full-surface-but-stale envelope passes the floor yet is stale.

    The W1254 additive trust axis: floor coverage (``passes``) and
    freshness (``stale``) are independent. A consumer that attests on
    ``passes`` alone would miss the staleness downgrade; this fixture
    forces consumers to read both.
    """
    packet = ChangeEvidence.from_canonical_json(load_consumer_fixture("consumer_stale"))
    floor = packet.assurance_floor()
    assert floor["passes"] is True, "stale envelope still has full floor coverage"
    assert floor["stale"] is True, "stale envelope MUST surface the stale flag"
    assert packet.evidence_stale is True
    assert packet.stale_reasons, "stale envelope MUST carry at least one reason"
    completeness = packet.evidence_completeness()
    assert completeness["stale"] is True
    # Staleness demotes every would-be-complete question to partial.
    assert completeness["complete"] == 0
