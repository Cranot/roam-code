from __future__ import annotations

from roam import observability_opt as oo
from roam import resilience as rs


def test_observability_harvester_delegates_to_resilience_policy():
    assert getattr(oo.harvest_source_files, "func") is rs.harvest_source_files
