"""Source/fixture contracts for the code atlas; not browser visual certification."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET

import pytest

from scripts.build_atlas_data import build, render_svg
from tests.test_homepage_contract import SITE, HomepageParser


def test_atlas_source_digest_is_portable_but_preserves_content_changes(tmp_path):
    source = tmp_path / "roam"
    source.mkdir()
    module = source / "app.py"
    module.write_bytes(b"def identity(value):\n    return value\n")
    lf = build(source)
    module.write_bytes(b"def identity(value):\r\n    return value\r\n")
    crlf = build(source)
    assert lf == crlf
    assert (
        lf["source_hash_definition"]
        == "SHA-256 of sorted relative POSIX paths and Python source bytes with CRLF normalized to LF, NUL-delimited"
    )
    module.write_bytes(b"def identity(value):\n    return value + 1\n")
    assert build(source)["source_sha256"] != lf["source_sha256"]


def test_generator_emits_portable_lf_bytes_in_an_isolated_project(tmp_path):
    """Run the real generator in a disposable repo-shaped fixture; no network."""
    import subprocess
    import sys

    from tests._helpers.repo_root import repo_root

    script = tmp_path / "scripts/build_atlas_data.py"
    script.parent.mkdir()
    script.write_bytes((repo_root() / "scripts/build_atlas_data.py").read_bytes())
    source = tmp_path / "src/roam"
    source.mkdir(parents=True)
    (source / "app.py").write_text("def identity(value):\n    return value\n", encoding="utf-8")
    destination = tmp_path / "templates/distribution/landing-page"
    destination.mkdir(parents=True)
    result = subprocess.run([sys.executable, str(script)], cwd=tmp_path, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    for name in ("atlas-data.json", "atlas-map.svg"):
        data = (destination / name).read_bytes()
        assert b"\n" in data and b"\r" not in data, name
    assert json.loads((destination / "atlas-data.json").read_bytes())["files_scanned"] == 1


def test_atlas_has_local_assets_native_fallback_and_named_controls():
    page = HomepageParser((SITE / "explore.html").read_text(encoding="utf-8"))
    ids = [node.attrs["id"] for node in page.elements if "id" in node.attrs]
    assert len(ids) == len(set(ids))
    assert len(list(page.root.find("h1"))) == 1
    assert any(node.attrs.get("src") == "/atlas-map.svg" for node in page.root.find("img"))
    assert len(list(page.root.find("noscript"))) == 1
    assert any(node.attrs.get("aria-label") == "Choose a code area" for node in page.root.find("select"))
    assert {node.attrs["data-mode"] for node in page.root.find("button") if "data-mode" in node.attrs} == {
        "all",
        "incoming",
        "outgoing",
    }
    for node in page.elements:
        assert not any(key.startswith("on") for key in node.attrs)
        asset = node.attrs.get("src")
        if asset:
            assert asset.startswith("/") and (SITE / asset.lstrip("/")).is_file()
    script = next(page.root.find("script"))
    assert script.attrs == {"type": "module", "src": "/atlas.mjs"}
    assert "not the native output" in page.root.text()
    assert "not proof of independence" in page.root.text() or "do not prove independence" in page.root.text()


def test_atlas_ci_runs_data_and_real_module_interaction_contracts():
    """Keep the dependency-free behavior checks on the existing hosted CI path."""
    import yaml

    from tests._helpers.repo_root import repo_root

    workflow = yaml.safe_load((repo_root() / ".github/workflows/roam-ci.yml").read_text(encoding="utf-8"))
    job = workflow["jobs"]["site-atlas"]
    scripts = "\n".join(step.get("run", "") for step in job["steps"])
    assert "node --test tests/atlas_model.test.mjs tests/atlas_interaction.test.mjs" in scripts
    assert "npm install" not in scripts and "npx" not in scripts
    for filename in ("index.html", "explore.html"):
        page = HomepageParser((SITE / filename).read_text(encoding="utf-8"))
        status = next(node for node in page.elements if "data-load-status" in node.attrs)
        assert status.attrs.get("role") == "status"
        retry = next(node for node in page.elements if "data-retry" in node.attrs)
        assert retry.tag == "button" and retry.attrs.get("type") == "button"
        assert "hidden" in retry.attrs and "disabled" not in retry.attrs


def test_public_snapshot_has_observed_denominator_and_no_dangling_areas():
    data = json.loads((SITE / "atlas-data.json").read_text(encoding="utf-8"))
    ids = {node["id"] for node in data["nodes"]}
    assert len(ids) == len(data["nodes"])
    assert data["files_scanned"] == sum(node["files"] for node in data["nodes"]) > 0
    assert len(data["source_sha256"]) == 64
    assert data["module_connections"] >= sum(edge["count"] for edge in data["edges"])
    assert all(edge["source"] in ids and edge["target"] in ids and edge["count"] > 0 for edge in data["edges"])
    ET.fromstring((SITE / "atlas-map.svg").read_text(encoding="utf-8"))


def test_atlas_theme_tokens_keep_body_and_control_copy_readable():
    """Palette math only: this does not certify rendered contrast or layout."""
    css = (SITE / "atlas.css").read_text(encoding="utf-8")
    shared = (SITE / "landing.css").read_text(encoding="utf-8")
    values = dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-f]{6}|var\(--[\w-]+\));", shared + css))
    aliases = dict(re.findall(r"--atlas-([a-z]+):var\((--[\w-]+)\);", css))
    assert (
        aliases.items()
        >= {"bg": "--bg", "ink": "--ink", "muted": "--muted", "accent": "--accent", "line": "--border"}.items()
    )

    def resolve(name):
        value = values[name]
        return resolve(value[4:-1]) if value.startswith("var(") else value

    tokens = {name: resolve("--atlas-" + name) for name in ("ink", "bg", "muted", "panel", "accent", "focus")}

    def luminance(color):
        channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
        return sum(value * weight for value, weight in zip(linear, (0.2126, 0.7152, 0.0722)))

    for foreground, background in [
        (tokens["ink"], tokens["bg"]),
        (tokens["muted"], tokens["panel"]),
        (tokens["muted"], tokens["bg"]),
        (tokens["accent"], tokens["panel"]),
        (tokens["panel"], tokens["accent"]),  # Primary action and active mode.
        (tokens["ink"], resolve("--accent-soft")),  # Connected node labels.
        (tokens["focus"], tokens["panel"]),  # Focus ring and load notice.
    ]:
        bright, dark = sorted((luminance(foreground), luminance(background)), reverse=True)
        assert (bright + 0.05) / (dark + 0.05) >= 4.5, (foreground, background)
    assert "color-scheme:light" in css
    assert ".atlas-page,.atlas-widget" in css  # Both presentations share the palette.
    assert ".atlas-node.is-dimmed { opacity:" not in css, "Keep de-emphasized labels readable"
    assert "prefers-reduced-motion:reduce" in css and "animation:none!important" in css
    assert "infinite" not in css


def test_static_fallback_and_home_preview_share_the_light_site_palette():
    shared = (SITE / "landing.css").read_text(encoding="utf-8")
    tokens = dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-f]{6});", shared))
    data = json.loads((SITE / "atlas-data.json").read_text(encoding="utf-8"))
    svg = ET.fromstring(render_svg(data))
    ns = {"svg": "http://www.w3.org/2000/svg"}
    assert svg.find("svg:rect", ns).attrib["fill"] == "#fffefa"
    labels = svg.findall("svg:text", ns)
    assert labels and all(label.attrib["fill"] == tokens["--ink"] for label in labels)
    assert all(label.attrib["stroke"] == "#fffefa" for label in labels)
    css = (SITE / "atlas.css").read_text(encoding="utf-8")
    home = (SITE / "home.css").read_text(encoding="utf-8")
    module = (SITE / "atlas.mjs").read_text(encoding="utf-8")
    assert "background:var(--atlas-panel)" in home
    assert "class:'atlas-arrow'" in module and ".atlas-arrow { fill:var(--atlas-accent); }" in css
    for old in ("#080f19", "#0e1b29", "#091522", "#83ece9", "#ffc18c"):
        assert old not in css + home + module + ET.tostring(svg, encoding="unicode")
    page = HomepageParser((SITE / "explore.html").read_text(encoding="utf-8"))
    theme = next(node for node in page.root.find("meta") if node.attrs.get("name") == "theme-color")
    assert theme.attrs["content"] == tokens["--bg"]


def test_static_svg_preserves_source_labels_and_resolves_its_paint_references():
    data = json.loads((SITE / "atlas-data.json").read_text(encoding="utf-8"))
    svg = ET.fromstring(render_svg(data))
    ns = {"svg": "http://www.w3.org/2000/svg"}
    assert [element.text for element in svg.findall("svg:text", ns)] == [node["label"] for node in data["nodes"]]
    for label, node in zip(svg.findall("svg:text", ns), data["nodes"]):
        expected = "start" if node["x"] < 150 else "end" if node["x"] > 810 else "middle"
        assert label.attrib["text-anchor"] == expected  # Keep edge labels inside the canvas.
    assert len(svg.findall("svg:circle", ns)) == len(data["nodes"]) * 3
    ids = [element.attrib["id"] for element in svg.iter() if "id" in element.attrib]
    assert len(ids) == len(set(ids))
    for element in svg.iter():
        for value in element.attrib.values():
            if value.startswith("url(#"):
                assert value[5:-1] in ids


def test_builder_resolves_relative_modules_deduplicates_and_discloses_gaps(tmp_path):
    (tmp_path / "commands").mkdir()
    (tmp_path / "db").mkdir()
    (tmp_path / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "db/__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "db/connection.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "commands/check.py").write_text(
        "from ..db import connection\nfrom roam.db.connection import value\n"
        "if False:\n    import roam.db.connection\nimport roam.missing\nimport os\n",
        encoding="utf-8",
    )
    data = build(tmp_path)
    assert data["files_scanned"] == 4
    assert data["module_connections"] == 1
    assert data["unresolved_local_imports"] == 1
    assert data["edges"] == [{"source": "commands", "target": "db", "count": 1}]
    assert data == build(tmp_path)
    ET.fromstring(render_svg(data))


def test_empty_or_unparsed_source_cannot_create_success_snapshot(tmp_path):
    with pytest.raises(ValueError, match="No Python"):
        build(tmp_path)
    (tmp_path / "broken.py").write_text("def broken(:", encoding="utf-8")
    with pytest.raises(SyntaxError):
        build(tmp_path)


def test_ambiguous_module_identity_cannot_drop_a_file(tmp_path):
    (tmp_path / "area").mkdir()
    (tmp_path / "area.py").write_text("", encoding="utf-8")
    (tmp_path / "area/__init__.py").write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="Ambiguous"):
        build(tmp_path)
