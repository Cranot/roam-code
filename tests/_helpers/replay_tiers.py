"""Render a served site file under chosen PR Replay tier states.

Served pages carry the copy their policy tier state selects; the other state
lives in ``dev/site-replay-offers``. Contracts about the copy of an open tier
read this rendering, so they keep guarding the offer while it is paused.
Pure text rendering: no writes, subprocesses or network.
"""

from __future__ import annotations

import json

from scripts import build_site_product_facts as builder
from tests._helpers.repo_root import repo_root

ROOT = repo_root()
OPEN = {"team": "OPEN", "deep": "OPEN"}


def rendered(name: str, states: dict[str, str] | None = None) -> str:
    """Return site file ``name`` (or ``README.md``) as the generator renders it for ``states``."""
    facts = builder.product_facts(ROOT)
    facts["replayTiers"] = {**facts["replayTiers"], **(states or {})}
    path, store = next((path, store) for path, relative, store in builder.served_files(ROOT) if relative == name)
    text = path.read_text(encoding="utf-8")
    variants = builder.parse_variants(store.read_text(encoding="utf-8"), name) if store.is_file() else None
    offers = json.loads((ROOT / builder.OFFERS / "offers.json").read_text(encoding="utf-8"))
    return builder.render(text, facts, variants, offers)
