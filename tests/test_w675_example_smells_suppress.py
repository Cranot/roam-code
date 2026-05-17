"""W675 — verify the shipped ``smells.suppress.yml`` example parses cleanly.

The example file lives at ``templates/examples/smells.suppress.yml`` and is
the customer-facing template for the W658 ``.roam/smells.suppress.yml``
substrate. This test loads it through the canonical loader to prove:

* The parser accepts the example body without any ``warnings_out`` entries.
* Every example entry round-trips through the W692
  :class:`KindSymbolSuppression` dataclass shape.
* Required fields (``kind``, ``symbol``) populate on every entry.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from roam.commands.smells_suppress import (
    load_smells_suppressions,
    load_smells_suppressions_typed,
)
from roam.policy.suppression_v2 import KindSymbolSuppression
from tests._helpers.repo_root import repo_root

EXAMPLE_PATH = repo_root() / "templates" / "examples" / "smells.suppress.yml"


def _stage_example(tmp_path: Path) -> Path:
    """Copy the shipped example into ``tmp_path/.roam/smells.suppress.yml``."""
    target_dir = tmp_path / ".roam"
    target_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(EXAMPLE_PATH, target_dir / "smells.suppress.yml")
    return tmp_path


def test_example_file_exists() -> None:
    """Assert the shipped example file is on disk and non-empty."""
    assert EXAMPLE_PATH.exists(), f"Missing shipped example at {EXAMPLE_PATH}"
    assert EXAMPLE_PATH.stat().st_size > 0


def test_example_parses_without_warnings(tmp_path: Path) -> None:
    """Load the example and assert zero warnings_out entries."""
    root = _stage_example(tmp_path)
    warnings_out: list[str] = []
    rows = load_smells_suppressions(root, warnings_out=warnings_out)
    assert warnings_out == [], f"Example must parse clean; got warnings: {warnings_out}"
    assert len(rows) >= 4, f"Expected >=4 example entries; got {len(rows)}"
    for row in rows:
        assert "kind" in row and row["kind"], f"Entry missing 'kind': {row}"
        assert "symbol" in row and row["symbol"], f"Entry missing 'symbol': {row}"


def test_example_roundtrips_through_typed_dataclass(tmp_path: Path) -> None:
    """Load via the typed loader and assert KindSymbolSuppression shape."""
    root = _stage_example(tmp_path)
    warnings_out: list[str] = []
    typed_rows = load_smells_suppressions_typed(root, warnings_out=warnings_out)
    assert warnings_out == [], f"Typed loader must parse clean; got: {warnings_out}"
    assert len(typed_rows) >= 4
    for entry in typed_rows:
        assert isinstance(entry, KindSymbolSuppression)
        assert entry.kind, f"Typed entry missing kind: {entry}"
        assert entry.symbol, f"Typed entry missing symbol: {entry}"
        assert entry.source == "smells-suppress-yml"
