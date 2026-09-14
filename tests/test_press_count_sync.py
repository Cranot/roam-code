"""Press fact markup must participate in the real count check/write path."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from tests._helpers.repo_root import repo_root

ROOT = repo_root()
PRESS = Path("templates/distribution/landing-page/press.html")


@pytest.fixture
def sync():
    spec = importlib.util.spec_from_file_location("press_count_sync", ROOT / "scripts/sync_surface_counts.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("field", ["commands", "mcp_tools", "mcp_core_tools", "languages"])
def test_press_count_change_is_detected_and_repaired(sync, tmp_path, monkeypatch, capsys, field):
    # Use the real public markup and real count producer. Only the selected
    # count is varied; version/tag scans are unrelated and stay stubbed. Run
    # the public main path in a disposable filesystem, never the real tree.
    counts = sync._live_counts()
    languages = sync._live_languages()
    path = tmp_path / PRESS
    path.parent.mkdir(parents=True)
    baseline = (ROOT / PRESS).read_text(encoding="utf-8")
    fragments = {
        "commands": f"<strong>{counts['commands']}</strong> CLI commands",
        "mcp_tools": f"<strong>{counts['mcp_tools']}</strong> MCP tools",
        "mcp_core_tools": f"({counts['mcp_core_tools']} in the default core preset)",
        "languages": f"<strong>{languages}</strong> programming languages",
    }
    previous = languages if field == "languages" else counts[field]
    old_fragment = fragments[field]
    assert baseline.count(old_fragment) == 1
    path.write_text(baseline, encoding="utf-8")
    monkeypatch.setattr(sync, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(sync, "_live_counts", lambda: counts)
    monkeypatch.setattr(sync, "_live_languages", lambda: languages)
    monkeypatch.setattr(sync, "_pyproject_version", lambda: "14.1.0")
    monkeypatch.setattr(sync, "_published_version", lambda: "14.1.0")
    monkeypatch.setattr(sync, "release_pin_drift", lambda *args, **kwargs: [])
    monkeypatch.setattr(sys, "argv", ["sync_surface_counts.py"])
    assert sync.main() == 0
    capsys.readouterr()
    if field == "languages":
        languages += 1
    else:
        counts[field] += 1
    assert sync.main() == 1, f"changed {field} did not cause press drift"
    assert PRESS.as_posix() in capsys.readouterr().out
    assert path.read_text(encoding="utf-8") == baseline, "check mode wrote the page"
    monkeypatch.setattr(sys, "argv", ["sync_surface_counts.py", "--write"])
    assert sync.main() == 0
    updated = path.read_text(encoding="utf-8")
    expected = old_fragment.replace(str(previous), str(previous + 1), 1)
    assert updated == baseline.replace(old_fragment, expected, 1)
    monkeypatch.setattr(sys, "argv", ["sync_surface_counts.py"])
    assert sync.main() == 0
    assert path.read_text(encoding="utf-8") == updated


def test_press_sync_leaves_unrelated_bold_numbers_intact(sync):
    text = "<p>Measured in July: <strong>3,900</strong> downloads. <strong>6</strong> examples.</p>"
    sync.build_replacements(sync._live_counts(), sync._live_languages())
    patterns = [patterns for path, patterns, _ in sync.iter_replacements() if path == ROOT / PRESS]
    assert patterns
    for group in patterns:
        for pattern, replacement in group:
            if replacement is not None:
                text = pattern.sub(replacement, text)
    assert text == "<p>Measured in July: <strong>3,900</strong> downloads. <strong>6</strong> examples.</p>"


def test_press_inventory_and_example_keep_their_scope():
    from tests.test_homepage_contract import HomepageParser, normalized

    page = HomepageParser((ROOT / PRESS).read_text(encoding="utf-8"))
    main = next(page.root.find("main"))
    text = normalized(main.text())
    assert "Source counts and their scope" in text
    assert "source registries" in text
    assert "not a fresh measurement of the package" in text
    assert "roam --json surface" in text
    assert "includes aliases" in text
    assert "Analysis depth varies by language and framework" in text
    assert "Stars are not a count of active users" in text
    assert "Authoritative counts as of" not in text
    assert "PyPI installs:" not in text
    assert "git diff | roam critique" not in text
    assert any(link.attrs.get("href") == "/#home-example-heading" for link in main.find("a"))


def test_homepage_example_action_reaches_captured_output_without_replacing_the_map():
    from tests.test_homepage_contract import SITE, HomepageParser, normalized

    page = HomepageParser((SITE / "index.html").read_text(encoding="utf-8"))
    header = next(page.root.find("header"))
    assert any(
        link.attrs.get("href") == "#home-example-heading" and "See a real example" in normalized(link.text())
        for link in header.find("a")
    )
    maps = [node for node in header.find("figure") if "data-atlas" in node.attrs]
    assert len(maps) == 1
    assert any(link.attrs.get("href") == "/explore" for link in maps[0].find("a"))
    section = next(
        node for node in page.root.find("section") if node.attrs.get("aria-labelledby") == "home-example-heading"
    )
    assert any(node.attrs.get("id") == "home-example-heading" for node in section.find("h2"))
    details = list(section.find("details"))
    assert len(details) == 1
    assert "Selected fields from roam --json impact calculate_total" in normalized(details[0].text())
    assert any(
        link.attrs.get("href") == "/data/examples/checkout-impact-2026-09-13.json" for link in details[0].find("a")
    )
