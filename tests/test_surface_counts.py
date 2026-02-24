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
    assert counts["command_names"] == 137
    assert counts["canonical_commands"] == 136
    assert counts["alias_names"] == 1
    assert counts["alias_groups"] == [["algo", "math"]]


def test_mcp_surface_counts():
    counts = mcp_surface_counts()
    assert counts["core_tools"] == 23
    assert counts["registered_tools"] == 101
    assert counts["duplicate_tool_names"] == []


def test_docs_use_reconciled_command_count_copy():
    expected = "136 canonical commands (+1 legacy alias = 137 invokable names)"
    assert expected in _read("README.md")
    assert expected in _read("CLAUDE.md")
    assert expected in _read("llms-install.md")


def test_collect_surface_counts_shape():
    payload = collect_surface_counts()
    assert set(payload.keys()) == {"cli", "mcp"}
    assert payload["cli"]["canonical_commands"] == 136
    assert payload["mcp"]["registered_tools"] == 101
