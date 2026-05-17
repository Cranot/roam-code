"""W676 follow-up — verify the 3 sibling suppression example files parse cleanly.

W675 shipped ``templates/examples/smells.suppress.yml`` as the canonical
template for the W658 substrate. This follow-up ships customer-facing
examples for the three remaining suppression substrates and pins their
loaders against the example bodies:

* ``templates/examples/.roamignore-findings``  -> W706 rules-file substrate
* ``templates/examples/suppressions.json``      -> W691 per-finding-hash substrate
* ``templates/examples/.roam-suppressions.yml`` -> W692 triage substrate

Each test stages the example into a temp project root, loads it through the
canonical loader with a ``warnings_out`` accumulator, asserts zero
warnings, and roundtrips through the typed dataclass surface where one
exists.
"""

from __future__ import annotations

import json as _json
import shutil
from pathlib import Path

from roam.commands.finding_suppress import (
    _load_ignore_findings_file as _load_ignore_findings,
)
from roam.commands.finding_suppress import (
    _load_per_finding_suppressions,
    load_per_finding_suppressions_typed,
)
from roam.commands.suppression import (
    load_suppressions,
    load_suppressions_typed,
)
from roam.policy.suppression_v2 import (
    FindingIdSuppression,
    RuleFileSuppression,
)

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "templates" / "examples"


def test_ignore_findings_example_exists() -> None:
    """Assert the .roamignore-findings example is on disk and non-empty."""
    path = EXAMPLES_DIR / ".roamignore-findings"
    assert path.exists(), f"Missing example at {path}"
    assert path.stat().st_size > 0


def test_ignore_findings_example_parses_clean(tmp_path: Path) -> None:
    """Load .roamignore-findings example and assert zero warnings_out + >=4 rules."""
    src = EXAMPLES_DIR / ".roamignore-findings"
    target = tmp_path / ".roamignore-findings"
    shutil.copy2(src, target)

    warnings_out: list[str] = []
    rules = _load_ignore_findings(target, warnings_out=warnings_out)
    assert warnings_out == [], f"Example must parse clean; got: {warnings_out}"
    assert len(rules) >= 4, f"Expected >=4 example rules; got {len(rules)}"
    for rule in rules:
        assert rule.get("task_id") or rule.get("path_glob"), f"Rule must declare task_id or path_glob: {rule}"


def test_suppressions_json_example_exists() -> None:
    """Assert the suppressions.json example is on disk and non-empty."""
    path = EXAMPLES_DIR / "suppressions.json"
    assert path.exists(), f"Missing example at {path}"
    assert path.stat().st_size > 0


def test_suppressions_json_example_parses_clean(tmp_path: Path) -> None:
    """Load suppressions.json via the canonical loader; assert zero warnings_out.

    The example file carries two metadata keys (`_comment`, `_schema_reference`)
    so users have inline documentation; strip those for the canonical loader
    so it sees only the finding_id-keyed entries.
    """
    src = EXAMPLES_DIR / "suppressions.json"
    raw = _json.loads(src.read_text(encoding="utf-8"))
    entries_only = {k: v for k, v in raw.items() if not k.startswith("_")}

    target_dir = tmp_path / ".roam"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "suppressions.json"
    target.write_text(_json.dumps(entries_only), encoding="utf-8")

    warnings_out: list[str] = []
    entries = _load_per_finding_suppressions(target, warnings_out=warnings_out)
    assert warnings_out == [], f"Example must parse clean; got: {warnings_out}"
    assert len(entries) >= 4, f"Expected >=4 example entries; got {len(entries)}"
    for finding_id_key, entry in entries.items():
        assert isinstance(finding_id_key, str) and finding_id_key
        assert entry.get("reason"), f"Entry missing reason: {entry}"


def test_suppressions_json_example_roundtrips_typed(tmp_path: Path) -> None:
    """Roundtrip the suppressions.json example through FindingIdSuppression."""
    src = EXAMPLES_DIR / "suppressions.json"
    raw = _json.loads(src.read_text(encoding="utf-8"))
    entries_only = {k: v for k, v in raw.items() if not k.startswith("_")}

    target_dir = tmp_path / ".roam"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "suppressions.json"
    target.write_text(_json.dumps(entries_only), encoding="utf-8")

    warnings_out: list[str] = []
    typed = load_per_finding_suppressions_typed(target, warnings_out=warnings_out)
    assert warnings_out == [], f"Typed loader must parse clean; got: {warnings_out}"
    assert len(typed) >= 4
    for entry in typed:
        assert isinstance(entry, FindingIdSuppression)
        assert entry.finding_id, f"Typed entry missing finding_id: {entry}"
        assert entry.reason, f"Typed entry missing reason: {entry}"
        assert entry.source == "suppressions-json"


def test_roam_suppressions_yml_example_exists() -> None:
    """Assert the .roam-suppressions.yml example is on disk and non-empty."""
    path = EXAMPLES_DIR / ".roam-suppressions.yml"
    assert path.exists(), f"Missing example at {path}"
    assert path.stat().st_size > 0


def test_roam_suppressions_yml_example_parses_clean(tmp_path: Path) -> None:
    """Load .roam-suppressions.yml example and assert zero warnings_out + >=4 rows."""
    src = EXAMPLES_DIR / ".roam-suppressions.yml"
    target = tmp_path / ".roam-suppressions.yml"
    shutil.copy2(src, target)

    warnings_out: list[str] = []
    rows = load_suppressions(tmp_path, warnings_out=warnings_out)
    assert warnings_out == [], f"Example must parse clean; got: {warnings_out}"
    assert len(rows) >= 4, f"Expected >=4 example rows; got {len(rows)}"
    for row in rows:
        assert row.get("rule"), f"Row missing rule: {row}"
        assert row.get("file"), f"Row missing file: {row}"


def test_roam_suppressions_yml_example_roundtrips_typed(tmp_path: Path) -> None:
    """Roundtrip the .roam-suppressions.yml example through RuleFileSuppression."""
    src = EXAMPLES_DIR / ".roam-suppressions.yml"
    target = tmp_path / ".roam-suppressions.yml"
    shutil.copy2(src, target)

    warnings_out: list[str] = []
    typed = load_suppressions_typed(tmp_path, warnings_out=warnings_out)
    assert warnings_out == [], f"Typed loader must parse clean; got: {warnings_out}"
    assert len(typed) >= 4
    for entry in typed:
        assert isinstance(entry, RuleFileSuppression)
        assert entry.rule, f"Typed entry missing rule: {entry}"
        assert entry.file, f"Typed entry missing file: {entry}"
        assert entry.source == "rule-file-yml"
