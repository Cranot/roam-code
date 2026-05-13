"""R27 — Codebase-law mining and enforcement.

This package implements the *self-installing constitution* concept from the
backlog: a tool that infers a repo's unwritten rules from its own code +
tests + git history, then enforces them against future PRs.

Public surface
--------------
* :class:`roam.laws.miner.Law` — the canonical law dataclass. Each law
  carries an ``id``, ``kind``, ``description``, ``evidence`` dict,
  ``severity`` / ``confidence`` labels, and a machine-readable ``rule``
  dict that other tooling (notably R18's policy DSL) can re-use.
* :func:`roam.laws.miner.mine_laws` — the entry point that walks the
  indexed DB + git history and returns the discovered laws.
* :func:`roam.laws.checker.check_laws` — runs a list of laws against a
  diff (working / staged / pr / file) and returns violations.
* :func:`roam.laws.serializer.dump_laws_yaml` /
  :func:`roam.laws.serializer.load_laws_yaml` — round-trip the laws
  through ``roam-laws.yml``.

The CLI surface lives in :mod:`roam.commands.cmd_laws`.
"""

from __future__ import annotations

from roam.laws.miner import Law, Violation, mine_laws
from roam.laws.checker import check_laws
from roam.laws.serializer import dump_laws_yaml, load_laws_yaml

__all__ = [
    "Law",
    "Violation",
    "mine_laws",
    "check_laws",
    "dump_laws_yaml",
    "load_laws_yaml",
]
