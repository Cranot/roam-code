"""The four public distribution manifests agree with the build they describe.

roam-code publishes itself through channels that each read a different file,
and none of them read ``pyproject.toml``:

* ``.claude-plugin/plugin.json`` — Claude Code plugin marketplace
* ``server.json``               — official MCP Registry
* ``glama.json``                — Glama MCP directory
* ``CITATION.cff`` / ``codemeta.json`` — citation + software-metadata harvesters

Every one of these is a hand-maintained restatement of facts the build already
knows, which is the standing precondition for drift. It had already happened:
``plugin.json`` sat at ``13.6.1`` while ``pyproject.toml`` shipped ``13.10.0``,
four releases later, because ``scripts/sync_surface_counts.py`` swept 52 sites
and this file was not one of them. The plugin ``version`` is not cosmetic —
Claude Code treats it as the update trigger, so a stale literal freezes every
installed copy on an old manifest, ``mcpServers`` block included.

The same file also advertised a "lean 16-tool MCP core preset". Both numbers in
that dispute are real and neither was wrong at the source: ``_CORE_TOOLS`` holds
16 names, and the ``core`` preset REGISTERS 17 because ``roam_expand_toolset``
is always on. Measured, not read:

    $ ROAM_MCP_PRESET=core roam mcp --list-tools
    17 tools registered (preset: core)

``server.json`` and the README marker block both say 17 and are correct. Rather
than add a third site that must be kept at 17, the count was removed from the
plugin prose entirely — a number that is not published cannot go stale. That is
what ``test_plugin_description_quotes_no_bare_tool_count`` locks in.

The version pins here are DERIVED from ``pyproject.toml`` (W1501), never frozen:
a literal in this module would agree with a manifest nobody bumped and fail the
release that bumped one correctly, which is the wrong signal in both directions.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from scripts import sync_surface_counts as sync
from tests._helpers.repo_root import repo_root

ROOT = repo_root()


def _json(rel: str) -> dict:
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def version() -> str:
    return sync._pyproject_version()


# ---------------------------------------------------------------------------
# Claude Code plugin manifest
# ---------------------------------------------------------------------------


def test_plugin_manifest_version_matches_pyproject(version: str) -> None:
    """The regression this module exists for.

    Claude Code pins an installed plugin to this string and only offers an
    update when it changes, so a stale value is not a cosmetic doc bug: it
    withholds every subsequent release from everyone who already installed.
    """
    actual = _json(".claude-plugin/plugin.json")["version"]
    assert actual == version, (
        f".claude-plugin/plugin.json version {actual!r} != pyproject {version!r}; "
        "run `python scripts/sync_surface_counts.py --write`"
    )


def test_plugin_description_quotes_no_bare_tool_count() -> None:
    """No literal tool count in the plugin prose.

    The published text said "16-tool core preset" while the server registers
    17. Re-stating a measured count in marketing prose buys nothing and has to
    be maintained forever; the authoritative counts live in ``server.json``'s
    preset description and the README auto-count marker blocks, both generated.
    """
    description = _json(".claude-plugin/plugin.json")["description"]
    stale = re.findall(r"\b\d+[\s-]tools?\b", description)
    assert not stale, (
        f"plugin.json description hardcodes a tool count {stale!r}. "
        "Counts belong in generated surfaces (server.json / README markers), not here."
    )


def test_plugin_manifest_states_the_pip_prerequisite() -> None:
    """A plugin install alone leaves the server unable to start.

    ``mcpServers.command`` is ``roam``, resolved from PATH by the OS in exec
    form -- there is no shell and no wrapper, so nothing of ours runs to
    explain the failure:

        $ PATH=... sh -c 'roam mcp'
        sh: line 1: roam: command not found     (exit 127)

    The exec-layer failure cannot be intercepted without making the plugin
    depend on some OTHER binary being present (a shell, or a specific
    ``python`` spelling), which just relocates the same failure. So the
    prerequisite is stated where a user reads BEFORE installing -- the
    description shown in the ``/plugin`` browser -- and this test keeps it
    there.
    """
    description = _json(".claude-plugin/plugin.json")["description"]
    assert "pip install roam-code" in description, (
        "plugin.json description must name the install prerequisite; without it a "
        "plugin-only install fails on every tool call with an unexplained exit 127"
    )
    assert "roam" in _json(".claude-plugin/plugin.json")["mcpServers"]["roam-code"]["command"]


# ---------------------------------------------------------------------------
# The preset split — deliberate, not drift
# ---------------------------------------------------------------------------


def test_plugin_and_repo_mcp_presets_differ_on_purpose() -> None:
    """``plugin.json`` says ``core``; ``.mcp.json`` says ``full``.

    Same server name, two answers, and that is correct: ``.mcp.json`` is this
    repo's OWN dev config, where agents dogfood the entire surface, while the
    plugin is what a consumer installs and wants a tight prompt for. The split
    is only defensible while it is deliberate, so both values are pinned here
    and the reasoning is in the README's MCP client-setup section. A future
    edit that "unifies" them has to delete an assertion that says why not.
    """
    plugin = _json(".claude-plugin/plugin.json")["mcpServers"]["roam-code"]
    repo = _json(".mcp.json")["mcpServers"]["roam-code"]

    assert plugin["env"]["ROAM_MCP_PRESET"] == "core"
    assert repo["env"]["ROAM_MCP_PRESET"] == "full"

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Which preset ships where" in readme, (
        "the plugin/.mcp.json preset split must stay documented in README.md; "
        "an undocumented disagreement between two published configs reads as a bug"
    )


def test_declared_presets_exist_in_the_live_preset_map() -> None:
    """Both declared presets are real names, not aspirational ones."""
    from roam.surface_counts import collect_surface_counts

    presets = collect_surface_counts()["mcp"]["preset_counts"]
    for rel in (".claude-plugin/plugin.json", ".mcp.json"):
        name = _json(rel)["mcpServers"]["roam-code"]["env"]["ROAM_MCP_PRESET"]
        assert name in presets, f"{rel} selects unknown preset {name!r}; known: {sorted(presets)}"


# ---------------------------------------------------------------------------
# MCP Registry ownership token
# ---------------------------------------------------------------------------
#
# The registry fetches the PUBLISHED PyPI ``info.description`` -- which is this
# repo's README, via ``pyproject.toml -> readme`` -- and looks for the literal
# ``mcp-name: <server name>``. Reimplemented here from the registry's own
# validator (internal/validators/registries/mcpname.go) so the token is checked
# the way the service checks it, including the trailing-boundary rule that
# stops a prefix of a longer name from satisfying the claim.


def _contains_mcp_name_token(content: str, server_name: str) -> bool:
    token = f"mcp-name: {server_name}"
    start = 0
    while (idx := content.find(token, start)) != -1:
        rest = content[idx + len(token) :]
        if rest == "" or not re.match(r"[A-Za-z0-9._/-]", rest[0]) or rest.startswith("-->") or rest.startswith("--!>"):
            return True
        start = idx + 1
    return False


def test_readme_carries_the_registry_ownership_token() -> None:
    """MCP Registry hard blocker: no token, no registration.

    Ownership validation reads the token out of the published description, so
    this assertion passing means the NEXT release can register -- it does not
    mean the currently published wheel can. Publishing is a separate, owner-
    gated step.
    """
    server_name = _json("server.json")["name"]
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert _contains_mcp_name_token(readme, server_name), (
        f"README.md must contain the literal 'mcp-name: {server_name}' followed by a "
        "boundary (space, newline, '<', or '-->'), or MCP Registry ownership validation fails"
    )


def test_readme_is_the_published_long_description() -> None:
    """The token only counts if THIS file is what PyPI publishes."""
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^readme\s*=\s*"README\.md"', pyproject, re.M), (
        "pyproject no longer publishes README.md as the long_description; the "
        "mcp-name token above is then in a file the registry never reads"
    )


# ---------------------------------------------------------------------------
# Citation / software metadata
# ---------------------------------------------------------------------------


def test_citation_cff_is_shaped_and_current(version: str) -> None:
    doc = yaml.safe_load((ROOT / "CITATION.cff").read_text(encoding="utf-8"))
    # Required by the citation-file-format 1.2.0 schema.
    for key in ("cff-version", "message", "title", "authors"):
        assert key in doc, f"CITATION.cff missing required key {key!r}"
    assert doc["cff-version"] == "1.2.0"
    assert doc["version"] == version, f"CITATION.cff version {doc['version']!r} != pyproject {version!r}"
    assert doc["license"] == "Apache-2.0"
    assert doc["repository-code"] == "https://github.com/Cranot/roam-code"
    assert [a.get("name") for a in doc["authors"]] == ["Cranot"]


def test_codemeta_is_shaped_and_current(version: str) -> None:
    doc = _json("codemeta.json")
    assert doc["@context"] == "https://w3id.org/codemeta/3.0"
    assert doc["@type"] == "SoftwareSourceCode"
    assert doc["version"] == version, f"codemeta.json version {doc['version']!r} != pyproject {version!r}"
    assert doc["license"] == "https://spdx.org/licenses/Apache-2.0"
    assert doc["codeRepository"] == "https://github.com/Cranot/roam-code"
    assert [a["name"] for a in doc["author"]] == ["Cranot"]


def test_every_manifest_agrees_on_licence_and_repository(version: str) -> None:
    """One assertion covering the fields a directory reviewer cross-checks."""
    assert _json(".claude-plugin/plugin.json")["license"] == "Apache-2.0"
    assert _json("server.json")["repository"]["url"] == "https://github.com/Cranot/roam-code"
    assert _json("server.json")["version"] == version
    assert _json("glama.json")["maintainers"] == ["Cranot"]


# ---------------------------------------------------------------------------
# Negative control — the sync gate must FAIL on each newly covered site
# ---------------------------------------------------------------------------
#
# Coverage that has never been shown to fail is indistinguishable from no
# coverage; that is precisely how plugin.json drifted for four releases under a
# script whose docstring claimed it synced everything.


@pytest.fixture
def fake_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A synthetic tree the real sweep engine treats like the checkout.

    ``_tracked_files`` is stubbed for a second reason worth recording: the
    sweep enumerates via ``git ls-files``, so an UNTRACKED file is invisible to
    it. When ``CITATION.cff`` and ``codemeta.json`` were first written they
    were not reported as drifted until they were staged -- "covered the day it
    lands" means the day it is tracked, not the day it is written.
    """

    def _build(files: dict[str, str]) -> Path:
        for rel, body in files.items():
            target = tmp_path / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8", newline="")
        monkeypatch.setattr(sync, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(sync, "_tracked_files", lambda: sorted(files))
        return tmp_path

    return _build


_STALE_PLUGIN = '{\n    "name": "roam-code",\n    "version": "13.6.1",\n    "mcpServers": {}\n}\n'
_STALE_CODEMETA = '{\n    "name": "roam-code",\n    "version": "13.6.1"\n}\n'
_STALE_CITATION = "cff-version: 1.2.0\ntitle: roam-code\nversion: 13.6.1\n"


def test_gate_reports_every_stale_distribution_manifest(fake_tree) -> None:
    fake_tree(
        {
            ".claude-plugin/plugin.json": _STALE_PLUGIN,
            "codemeta.json": _STALE_CODEMETA,
            "CITATION.cff": _STALE_CITATION,
        }
    )
    drift = sync.release_pin_drift("14.0.0")
    assert {line.split(":", 1)[0] for line in drift} == {
        ".claude-plugin/plugin.json",
        "codemeta.json",
        "CITATION.cff",
    }, drift


def test_write_mode_repairs_every_distribution_manifest(fake_tree) -> None:
    tree = fake_tree(
        {
            ".claude-plugin/plugin.json": _STALE_PLUGIN,
            "codemeta.json": _STALE_CODEMETA,
            "CITATION.cff": _STALE_CITATION,
        }
    )
    assert sync.release_pin_drift("14.0.0", write=True) != []
    assert sync.release_pin_drift("14.0.0") == []
    assert json.loads((tree / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))["version"] == "14.0.0"
    assert json.loads((tree / "codemeta.json").read_text(encoding="utf-8"))["version"] == "14.0.0"
    assert "version: 14.0.0" in (tree / "CITATION.cff").read_text(encoding="utf-8")


def test_cff_version_key_is_not_swept(fake_tree) -> None:
    """False-positive control: ``cff-version: 1.2.0`` is a FORMAT version.

    It is the one line in these files that looks like a version, sits next to
    the one that is, and must never move. Rewriting it would declare the file
    to be in a citation-file-format revision that does not exist.
    """
    tree = fake_tree({"CITATION.cff": _STALE_CITATION})
    sync.release_pin_drift("14.0.0", write=True)
    assert "cff-version: 1.2.0" in (tree / "CITATION.cff").read_text(encoding="utf-8")
