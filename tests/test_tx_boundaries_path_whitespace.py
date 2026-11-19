"""Path constructor spacing must not hide filesystem mutations."""

import pytest

from roam.world_model.side_effects import SideEffectClassification
from roam.world_model.tx_boundaries import _classify_one


@pytest.mark.parametrize("spacing", ["", " ", "\t"])
def test_path_mutations_counted_inside_and_outside_transaction(spacing):
    # Source is analyzed as text; no filesystem mutations are executed.
    body = (
        "def change_files(cfg):\n"
        "    with db.transaction():\n"
        f"        Path{spacing}(cfg).unlink()\n"
        "        db.commit()\n"
        f"    Path{spacing}(cfg).touch()\n"
    )
    effects = SideEffectClassification(symbol="change_files", file="example.py", kinds=["io_write"])

    result = _classify_one(effects, body, 1)

    assert result.mutations_inside == 1
    assert result.mutations_outside == 1
    assert result.classification == "partial_transactional"
