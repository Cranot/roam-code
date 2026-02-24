import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _unreleased_section() -> str:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    m = re.search(r"## \[Unreleased\](.*?)(?:\n## \[|\Z)", text, re.S)
    assert m, "Missing [Unreleased] section in CHANGELOG.md"
    return m.group(1)


def test_unreleased_has_20_plus_items_with_ids():
    section = _unreleased_section()
    ids = set(int(x) for x in re.findall(r"#(\d+)", section))
    assert len(ids) >= 20, f"Expected >=20 backlog IDs in [Unreleased], got {len(ids)}"


def test_unreleased_covers_shipped_sessions_2_5_items():
    section = _unreleased_section()
    ids = set(int(x) for x in re.findall(r"#(\d+)", section))
    required = {
        23,
        38, 39, 40, 41,
        42, 43, 44, 57, 65, 68,
        74, 75, 77, 80, 81, 82, 83,
        84, 85, 86, 87, 88, 89,
        90, 91, 92, 94,
        97, 98, 99, 100, 101, 102, 103,
        105, 106, 108,
    }
    missing = sorted(required - ids)
    assert not missing, f"[Unreleased] missing shipped IDs: {missing}"
