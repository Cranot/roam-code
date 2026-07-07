"""R27 — Codebase-law mining and enforcement.

This package implements the *self-installing constitution* concept from the
backlog: a tool that infers a repo's current unwritten conventions from
its own code + tests, then enforces them against future PRs. (The git
co-change and error-handling strategies are v1 stubs returning ``[]`` —
see ``miner.py`` strategies D and E — so the shipped miner reads the
current index snapshot, not git history.)

Public surface
--------------
* :class:`roam.laws.miner.Law` — the canonical law dataclass. Each law
  carries an ``id``, ``kind``, ``description``, ``evidence`` dict,
  ``severity`` / ``confidence`` labels, and a machine-readable ``rule``
  dict that other tooling (notably R18's policy DSL) can re-use.
* :func:`roam.laws.miner.mine_laws` — the entry point that reads the
  indexed codebase's current conventions and returns the discovered laws.
* :func:`roam.laws.checker.check_laws` — runs a list of laws against a
  diff (working / staged / pr / file) and returns violations.
* :func:`roam.laws.serializer.dump_laws_yaml` /
  :func:`roam.laws.serializer.load_laws_yaml` — round-trip the laws
  through ``roam-laws.yml``.

The CLI surface lives in :mod:`roam.commands.cmd_laws`.
"""

from __future__ import annotations

from roam.laws.checker import check_laws
from roam.laws.miner import Law, Violation, mine_laws
from roam.laws.serializer import dump_laws_yaml, load_laws_yaml

__all__ = [
    "Law",
    "Violation",
    "mine_laws",
    "check_laws",
    "dump_laws_yaml",
    "load_laws_yaml",
]
