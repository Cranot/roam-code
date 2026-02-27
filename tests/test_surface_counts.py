import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roam.surface_counts import cli_surface_counts, collect_surface_counts, mcp_surface_counts


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_cli_surface_counts():
    counts = cli_surface_counts()
    assert counts["command_names"] == 143
    assert counts["canonical_commands"] == 140
    assert counts["alias_names"] == 3
    assert counts["alias_groups"] == [["algo", "math"], ["churn", "weather"], ["onboard", "understand"]]


def test_mcp_surface_counts():
    counts = mcp_surface_counts()
    assert counts["core_tools"] == 23
    assert counts["registered_tools"] == 102
    assert counts["duplicate_tool_names"] == []


def test_docs_use_reconciled_command_count_copy():
    expected = "140 commands"
    assert expected in _read("README.md")
    if Path("CLAUDE.md").exists():
        assert expected in _read("CLAUDE.md")
    assert expected in _read("llms-install.md")


def test_collect_surface_counts_shape():
    payload = collect_surface_counts()
    assert set(payload.keys()) == {"cli", "mcp"}
    assert payload["cli"]["canonical_commands"] == 140
    assert payload["mcp"]["registered_tools"] == 102
