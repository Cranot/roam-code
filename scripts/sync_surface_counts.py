#!/usr/bin/env python3
"""Sync surface counts (commands / MCP tools / languages) AND the release
version across every doc / template / registry surface that quotes them.

Two independent passes with two different sources of truth:

1. **Surface counts** — ``roam.surface_counts.collect_surface_counts()``.
2. **Release version** (W1501) — TWO sources, because version literals are
   not one class. ``identity`` literals (what this artifact calls itself:
   ``CITATION.cff``, ``codemeta.json``, the plugin manifest, generated doc
   headers) sync from ``pyproject.toml -> version``. ``install`` literals
   (what a reader or a machine is told to FETCH: ``roam-code==X``,
   ``Cranot/roam-code@vX``, the composite action's ``version`` input and
   default, ``server.json``'s package pin) sync from the last PUBLISHED
   release — the highest ``v*`` tag — because a version that is not published
   cannot be installed. Historical records are exempt by explicit registry;
   see ``_VERSION_PIN_EXEMPT`` and the class taxonomy below it.

For the count pass, this script reads the live counts and rewrites every
doc-surface that quotes them: server.json, the mcp-server-card family (below),
the Cloudflare-served landing-page HTML / llms.txt / docs pages, the
Claude Code skill, the in-repo CI integration doc, AND the **free-form
(non-marker) count phrases** in README.md / CLAUDE.md / AGENTS.md /
CONTRIBUTING.md (directory-tree comments, MCP-section prose, the
contributor reference table).

See also: ``dev/build_readme_counts.py``
    The two scripts are intentional cousins, not duplicates. This one
    (``sync_surface_counts.py``) handles **free-form prose surfaces**
    via regex substitution: the landing-page HTML pages, llms.txt,
    ``server.json``, ``skills/roam/SKILL.md``, ``competitor_site_data.py``,
    ``docs/ci-integration.md``, and the prose count phrases in
    README/CLAUDE/AGENTS/CONTRIBUTING that fall OUTSIDE the auto-count
    marker blocks. ``dev/build_readme_counts.py`` handles the
    **marker-protected Markdown blocks** (README, CLAUDE, AGENTS,
    llms-install) and the **two mcp-server-card.json files** (byte-
    identical, required by ``test_bundled_card_matches_public_card``).
    To keep the two scripts strictly non-overlapping, the README /
    CLAUDE / AGENTS entries here are flagged ``marker_aware=True`` — they
    substitute on a marker-MASKED copy so they can never rewrite a byte
    the cousin script owns. The mcp-server-card entries below are
    intentionally no-op (``repl=None``) — those cards are owned by
    ``build_readme_counts.py``; the entries are retained here only so
    reviewers can see the file is explicitly covered elsewhere.

CI runs both scripts back-to-back in the ``doc-hygiene`` job
(.github/workflows/roam-ci.yml). Either failing is a hard gate.

Usage:
    python scripts/sync_surface_counts.py            # dry-run (report only)
    python scripts/sync_surface_counts.py --write    # rewrite files in place

CI usage:
    python scripts/sync_surface_counts.py            # exit 1 if drift detected
    python scripts/sync_surface_counts.py --write    # rewrite + re-commit
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _live_counts() -> dict:
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from roam.surface_counts import collect_surface_counts

    surface = collect_surface_counts()
    return {
        "commands": int(surface["cli"]["command_names"]),
        "canonical": int(surface["cli"]["canonical_commands"]),
        "alias_names": int(surface["cli"]["alias_names"]),
        "specialised": int(surface["cli"]["command_names"]) - 5,  # 5-verb model
        "mcp_tools": int(surface["mcp"]["registered_tools"]),
        # Core-preset tool count from the live AST parser; never hardcode
        # this literal — it drifts the moment _CORE_TOOLS in mcp_server.py
        # changes (W933-class stale-literal hazard). See preset_counts in
        # roam.surface_counts.mcp_surface_counts.
        "mcp_core_tools": int(surface["mcp"]["preset_counts"]["core"]),
        "mcp_preset_counts": {str(name): int(count) for name, count in surface["mcp"]["preset_counts"].items()},
    }


def _live_languages() -> int:
    """Count of supported languages from the registry.

    Hard-fails on import error: this script is the source of truth for
    the language count quoted in README/llms-install/landing-page. A
    silent ``return 0`` would write ``0 languages`` into every doc
    surface — exactly the W933-class stale-literal hazard the sibling
    ``_live_counts`` deliberately avoids by letting errors propagate
    (see lines 58-64 above). Lineage rule (CLAUDE.md "Make fallback
    chains loud"): a sync tool with no producer must crash loudly so
    CI catches it, not silently mis-publish.
    """
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from roam.languages.registry import get_supported_languages

    return len(get_supported_languages())


# ---------------------------------------------------------------------------
# W1501 — release-version pins
# ---------------------------------------------------------------------------
#
# CONTRIBUTING has claimed since v11 that ``pyproject.toml -> version`` is the
# single source of truth and that "everything else syncs from it via
# scripts/sync_surface_counts.py". That was false for the VERSION: this script
# only ever synced surface COUNTS. Every release-pin literal — the PyPI pin in
# the shipped CI templates, the ``Cranot/roam-code@vX.Y.Z`` action refs, the
# composite action's own ``version`` input default, the MCP registry package
# pin in ``server.json`` — had to be remembered by hand, and a release that
# bumped only ``pyproject.toml`` would ship an action whose DEFAULT installs
# the PREVIOUS version. The claim survived because nothing ever failed on it.
#
# FOUR classes of occurrence exist, and the first two have OPPOSITE truth
# conditions. Conflating them is what made public ``main`` ship install
# instructions for a version that did not exist:
#
#   identity    — a literal that DESCRIBES this artifact (``CITATION.cff``,
#                 ``codemeta.json``, the plugin manifest version, the
#                 generated ``docs/COMMANDS.md`` header). Must equal
#                 ``pyproject.toml -> version``. Correct the moment the bump
#                 lands, because the tree at that commit really is that
#                 version.
#   install     — a literal that INSTRUCTS a reader or a machine to FETCH
#                 something (``roam-code==X``, ``roam-code[mcp]==X``,
#                 ``Cranot/roam-code@vX``, the composite action's ``version``
#                 input and its default, the roam-guard templates'
#                 ``actual == 'X'`` post-install assertion, ``server.json``'s
#                 ``packages[].version``). Must equal the last PUBLISHED
#                 release — never the declared one. A version that has not
#                 been published cannot be installed, so syncing this class
#                 at BUMP time guarantees every install instruction on main
#                 is a lie for the whole bump-to-release window.
#   historical  — records what was measured / built / shipped at a past
#                 version. Rewriting one falsifies a record, which is strictly
#                 worse than a stale string. See ``_VERSION_PIN_EXEMPT``.
#   lagging     — cannot legally equal EITHER source yet. Also
#                 ``_VERSION_PIN_EXEMPT``.
#
# ``install`` pins therefore move when the RELEASE moves, not when the bump
# lands: ``_published_version()`` reads the highest ``v*`` tag in this
# repository and hard-fails rather than guessing. Whether the version those
# pins name can actually be fetched is a separate question this script does
# not answer — ``scripts/check_install_targets.py`` is the gate for that, and
# it fails closed when it cannot reach the tag list.
#
# The patterns are SHAPE-anchored, not value-anchored: they match
# ``roam-code==<v>`` / ``Cranot/roam-code@v<v>`` and friends rather than the
# current literal. That is what lets a new file be covered the day it lands
# instead of the day someone remembers to register it — and it is why prose
# that merely names a version ("measured against the shipped 13.10.0 binary")
# is never touched: it carries no pin shape.

# Named group so ONE pattern list serves both the rewriter here and the
# scanner in ``scripts/check_install_targets.py``. Two lists would be two
# things to keep in agreement, and the gate that checks install targets exist
# must read exactly the sites this script rewrites — not a copy of them.
# No single pattern below may use this twice (duplicate group name).
_PIN_VERSION = r"(?P<ver>\d+\.\d+(?:\.\d+)?)"

# path (repo-relative, posix) -> why this file's pin shape must NOT be synced.
# A stale key is itself drift: ``tests/test_w1501_release_version_pins.py``
# fails if any path here has stopped existing.
_VERSION_PIN_EXEMPT: dict[str, str] = {
    # NOTE: ``.github/workflows/roam.yml`` used to be exempt here, on the
    # grounds that this repo consumes its OWN published action and so "cannot
    # be the version being cut". That was the correct OBSERVATION filed as the
    # wrong FIX: the same is true of every install pin in the tree, and
    # exempting the one file that got it right left the ~50 that got it wrong
    # force-synced forward. It is no longer exempt — under the ``install``
    # class it is now correct by construction, and it is the shape the rest of
    # the tree follows rather than the exception to it.
    #
    # Historical: a dated sample deliverable generated from a real run at
    # 12.25. Re-stamping it would claim the sample was produced by a version
    # that never saw it.
    "templates/audit-report/sample-redacted.md": (
        "dated sample audit (2026-05-05) recording a real run at roam-code "
        "12.25 — a recorded measurement, not a live install instruction"
    ),
    # Fixture: this module's negative control is a set of DELIBERATELY stale
    # pins. Syncing them would silently convert the proof that the gate fails
    # into a tautology that can only pass — the precise failure mode the whole
    # module exists to rule out. Any future module that builds synthetic pin
    # fixtures belongs here for the same reason.
    "tests/test_w1501_release_version_pins.py": (
        "negative-control fixtures are intentionally stale pins; syncing them "
        "would disarm the proof that this gate can fail"
    ),
    "tests/test_w1502_install_targets_exist.py": (
        "negative-control fixtures pin a DELIBERATELY unreleased version; "
        "syncing them to the published release would make the install-target "
        "gate's failure case unreachable, i.e. green by construction"
    ),
}


def _pyproject_version() -> str:
    """The one true version. Hard-fails rather than guessing."""
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if m is None:
        raise SystemExit("pyproject.toml has no [project] version — cannot sync release pins")
    return m.group(1)


_RELEASE_TAG = re.compile(r"^v(\d+)\.(\d+)(?:\.(\d+))?$")


def release_tags() -> list[str]:
    """Every final-release ``vX.Y[.Z]`` tag in this repository.

    Hard-fails when git cannot answer. An unreachable tag list rendered as
    "no tags, assume pyproject" is precisely the defect this whole module
    exists to close: it would turn the install class silently back into the
    declared version, i.e. back into shipping a pin for a release that does
    not exist. UNKNOWN refuses; it never defaults.
    """
    import subprocess

    proc = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "tag", "--list", "v*"],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(
            "release-pin sweep needs `git tag --list` to learn which release is "
            "PUBLISHED; the install-pin class cannot be resolved without it. "
            f"git exited {proc.returncode}: {proc.stderr.decode('utf-8', 'replace').strip()}"
        )
    out = proc.stdout.decode("utf-8", "surrogateescape")
    return [t for t in (line.strip() for line in out.splitlines()) if _RELEASE_TAG.fullmatch(t)]


def _published_version() -> str:
    """The last PUBLISHED release — the highest final-release ``v*`` tag.

    This is the source of truth for the ``install`` pin class. The tag is used
    rather than PyPI deliberately: it is offline, deterministic, and it is the
    exact ref a ``Cranot/roam-code@vX`` pin resolves against. The residual is
    stated rather than implied — a tag can exist while the publish workflow
    failed, so the tag proves the GitHub half of an install instruction and
    only implies the PyPI half. ``scripts/check_install_targets.py --pypi``
    closes the other half when a network is available, and reports UNKNOWN
    (not OK) when it is not.
    """
    tags = release_tags()
    if not tags:
        raise SystemExit(
            "no final-release `v*` tag exists in this repository, so the last "
            "PUBLISHED release is UNKNOWN and install pins cannot be resolved. "
            "Refusing rather than falling back to the declared version — that "
            "fallback is the defect this class split removes. If this is a "
            "shallow clone, fetch tags (`git fetch --tags`, or checkout with "
            "fetch-depth: 0)."
        )
    return max(tags, key=lambda t: tuple(int(p or 0) for p in _RELEASE_TAG.fullmatch(t).groups()))[1:]


def _install_sweep_patterns(published: str) -> list[tuple[re.Pattern, str]]:
    """INSTALL-class pin shapes swept across EVERY tracked text file.

    Every one of these tells a reader or a machine to FETCH something, so all
    of them take ``published`` — the last released version — and never the
    declared one.
    """
    version = published
    return [
        # `pip install "roam-code==X"`, `roam-code[mcp]==X`.
        (re.compile(rf"(roam-code(?:\[[a-z0-9,._-]+\])?==){_PIN_VERSION}"), rf"\g<1>{version}"),
        # `uses: Cranot/roam-code@vX` — the published composite action.
        (re.compile(rf"(Cranot/roam-code@v){_PIN_VERSION}"), rf"\g<1>{version}"),
        # The action's `version:` INPUT, anchored to a roam-code `uses:` line
        # within the same `with:` block. Anchoring is load-bearing: a bare
        # `^\s*version:` would also rewrite setup-uv's pinned `0.11.29` and
        # CircleCI's `version: 2.1`, neither of which is a roam version.
        (
            re.compile(rf"(Cranot/roam-code@[^\s\n]+\n(?:[^\n]*\n){{0,8}}?[ \t]*version:[ \t]*['\"]){_PIN_VERSION}"),
            rf"\g<1>{version}",
        ),
        # The post-install equality check the shipped roam-guard templates run
        # (`raise SystemExit(0 if actual == 'X' else 1)`). It must agree with
        # the pin above it or the template fails closed on every run.
        (re.compile(rf"(actual == ['\"]){_PIN_VERSION}"), rf"\g<1>{version}"),
    ]


def _install_structural_patterns(published: str) -> dict[str, list[tuple[re.Pattern, str]]]:
    """INSTALL-class sites with no reusable shape — anchored per file.

    Kept as its own registry rather than as a comment inside one merged dict,
    so the classification is DATA. ``scripts/check_install_targets.py`` reads
    exactly this registry plus ``_install_sweep_patterns`` to decide what
    counts as an install instruction. If the class lived only in a comment,
    the gate and the rewriter could disagree about which sites are install
    sites and nothing would say so.
    """
    return {
        # THE release-breaking one: the composite action's default install
        # target. Before the class split this was synced forward at bump time,
        # which shipped `pip install "roam-code==<unpublished>"` to every
        # consumer pinning `@main` — a hard install failure. It now names the
        # last published release, which is the only version the unguarded
        # `pip install` at the bottom of action.yml can resolve.
        "action.yml": [
            (
                re.compile(rf"(^  version:\n(?:^ {{4}}[^\n]*\n){{0,6}}?^    default: '){_PIN_VERSION}", re.M),
                rf"\g<1>{published}",
            ),
        ],
        # The MCP registry's PyPI package pin — the field that decides what a
        # registry client actually downloads. Its sibling, ``server.json``'s
        # top-level ``version``, is IDENTITY and lives in the other registry:
        # one file, both classes, which is why the split is per-pattern rather
        # than per-file.
        "server.json": [
            (
                re.compile(rf'("identifier": "roam-code",\s*\n\s*"version": "){_PIN_VERSION}'),
                rf"\g<1>{published}",
            ),
        ],
        # The documented default for the action input above; they must agree,
        # so it takes the same source. A reader who copies this table row into
        # their own `with:` block is issuing a fetch.
        "docs/ci-integration.md": [
            (re.compile(rf"(\| `version` \| `){_PIN_VERSION}"), rf"\g<1>{published}"),
        ],
    }


def _identity_structural_patterns(version: str) -> dict[str, list[tuple[re.Pattern, str]]]:
    """IDENTITY-class sites with no reusable shape — anchored per file.

    These surfaces state what this artifact IS, to a machine (registry and
    package metadata) or to a reader (generated doc headers). Each pattern is
    anchored on surrounding structure so it can only ever match the one
    intended field.
    """
    return {
        # Claude Code plugin marketplace manifest. ``version`` here is not
        # cosmetic: the docs make it the update trigger — "Setting this pins
        # the plugin to that version string, so users only receive updates
        # when you bump it." A stale literal therefore means every installed
        # plugin keeps serving the OLD manifest (including its mcpServers
        # block) no matter how many releases ship. It sat at 13.6.1 across
        # four releases because nothing swept it: the value carries no pin
        # shape, and the file is not in the count registry either. Same
        # anchored-JSON-key treatment as server.json below.
        ".claude-plugin/plugin.json": [
            (re.compile(rf'(^    "version": "){_PIN_VERSION}', re.M), rf"\g<1>{version}"),
        ],
        # IDENTITY. Citation metadata. Both state the version a citer would reference, so
        # a stale literal here mis-attributes published work to a release that
        # never contained the cited behaviour. Registered the day the files
        # landed rather than the day someone notices — the exact gap that let
        # plugin.json sit four releases behind.
        #
        # ``^version:`` cannot collide with CITATION.cff's ``cff-version:``
        # (that line does not start with ``version``), and the codemeta key is
        # anchored to its four-space indent like the two JSON manifests above.
        "CITATION.cff": [
            (re.compile(rf"(^version: ){_PIN_VERSION}", re.M), rf"\g<1>{version}"),
        ],
        "codemeta.json": [
            (re.compile(rf'(^    "version": "){_PIN_VERSION}', re.M), rf"\g<1>{version}"),
        ],
        # MCP registry, IDENTITY half only: what this server calls itself.
        # Its ``packages[].version`` sibling is INSTALL and lives in the other
        # registry. Syncing both forward at bump time told every registry
        # consumer to fetch a wheel that does not exist.
        "server.json": [
            (re.compile(rf'(^    "version": "){_PIN_VERSION}', re.M), rf"\g<1>{version}"),
        ],
        # Generated index header. `tests/test_commands_doc_synced.py`
        # deliberately canonicalizes this token away (the generator reads
        # INSTALLED metadata, which differs per dev environment), so nothing
        # pinned it to the repo's own version. Syncing it from pyproject here
        # makes it deterministic instead of environment-dependent.
        "docs/COMMANDS.md": [
            (re.compile(rf"(· roam v){_PIN_VERSION}"), rf"\g<1>{version}"),
        ],
        # Landing-page prose that states the CURRENT surface's version. The
        # existing sweep in tests/test_doc_consistency.py only recognises
        # `softwareVersion` and `current: vX`, so these two phrasings were
        # unguarded.
        "templates/distribution/landing-page/docs/agent-contract.html": [
            (re.compile(rf"(\(v){_PIN_VERSION}(?=\))"), rf"\g<1>{version}"),
        ],
        "templates/distribution/landing-page/docs/integration-tutorials.html": [
            (re.compile(rf"(Surface scale on v){_PIN_VERSION}"), rf"\g<1>{version}"),
        ],
        # The three landing-page fields that tests/test_doc_consistency.py
        # already FAILS on when they lag — they were gated but never
        # autofixed, so every release re-did them by hand. Same patterns the
        # scrapers in that module use, so gate and fixer cannot disagree.
        "templates/distribution/landing-page/index.html": [
            (re.compile(rf'("softwareVersion"\s*:\s*"v?){_PIN_VERSION}'), rf"\g<1>{version}"),
        ],
        "templates/distribution/landing-page/status.html": [
            (re.compile(rf"(current:\s*v){_PIN_VERSION}"), rf"\g<1>{version}"),
        ],
        "templates/distribution/landing-page/docs/canonical-demo.html": [
            (
                re.compile(rf'("name"\s*:\s*"roam"\s*,\s*"version"\s*:\s*"){_PIN_VERSION}'),
                rf"\g<1>{version}",
            ),
        ],
    }


def _tracked_files() -> list[str]:
    """Repo-relative posix paths of every tracked file.

    Hard-fails when git is unavailable. A silent fallback here would turn the
    coverage half of this gate into a no-op that still prints "in sync" — the
    same shape of unfalsifiable guarantee this whole function exists to close.
    """
    import subprocess

    proc = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z"],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(
            "release-pin sweep needs `git ls-files` to enumerate tracked files; "
            f"git exited {proc.returncode}: {proc.stderr.decode('utf-8', 'replace').strip()}"
        )
    return [p for p in proc.stdout.decode("utf-8", "surrogateescape").split("\0") if p]


def _structural_pin_patterns(version: str, published: str) -> dict[str, list[tuple[re.Pattern, str]]]:
    """The two structural registries merged, per file.

    ``server.json`` carries one pattern from each, so the merge is by
    concatenation rather than by ``dict`` update — a plain update would drop
    one of them and the loss would be silent.
    """
    merged: dict[str, list[tuple[re.Pattern, str]]] = {}
    for registry in (_install_structural_patterns(published), _identity_structural_patterns(version)):
        for rel, patterns in registry.items():
            merged.setdefault(rel, []).extend(patterns)
    return merged


def install_pin_sites() -> list[tuple[str, int, str]]:
    """Every INSTALL-class pin in the tree, as ``(path, line, version)``.

    The one place that answers "what does this repository tell people to
    fetch?". It reads the same two registries the rewriter writes through, at
    a sentinel version, so the gate in ``scripts/check_install_targets.py``
    cannot drift out of agreement with what ``--write`` actually rewrites.

    Deliberately says nothing about whether those versions EXIST — that is the
    caller's question, and it is the one this repository had no gate for.
    """
    # The patterns are independent of the version passed in; only the
    # replacement strings use it. The sentinel makes that explicit rather than
    # implying the argument is meaningful here.
    sentinel = "0.0.0"
    sweep = [pat for pat, _ in _install_sweep_patterns(sentinel)]
    structural = {rel: [pat for pat, _ in pats] for rel, pats in _install_structural_patterns(sentinel).items()}

    sites: list[tuple[str, int, str]] = []
    for rel in _tracked_files():
        if rel in _VERSION_PIN_EXEMPT:
            continue
        try:
            data = (REPO_ROOT / rel).read_bytes()
        except OSError:
            continue
        if b"roam" not in data:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        for pat in sweep + structural.get(rel, []):
            for m in pat.finditer(text):
                sites.append((rel, text.count("\n", 0, m.end()) + 1, m.group("ver")))
    return sites


def _trim_pin_context(match_text: str, limit: int = 64) -> str:
    """Collapse a (possibly multi-line) match to its version-bearing tail.

    The anchored patterns can span a `uses:` line plus a `with:` block; echoing
    the whole match buries the one thing the reader needs — which version is
    wrong. Keep the tail, which always ends at the version literal.
    """
    flat = " ".join(match_text.split())
    return flat if len(flat) <= limit else "..." + flat[-limit:]


def release_pin_drift(version: str, published: str, *, write: bool = False) -> list[str]:
    """Every release-version pin that disagrees with its class's source.

    ``version`` is the DECLARED version (``pyproject.toml``) and governs the
    ``identity`` class; ``published`` is the last RELEASED version and governs
    the ``install`` class. Both are required positionally on purpose: a
    defaulted ``published`` would silently restore the single-source
    behaviour that made main ship unresolvable install instructions, and it
    would do so at exactly the call site that forgot to pass it.

    Returns one ``"<path>: '<before>' -> '<after>'"`` line per drifted site.
    With ``write=True`` the files are rewritten in place and the same list is
    returned (so the caller can report what it fixed).
    """
    sweep = _install_sweep_patterns(published)
    structural = _structural_pin_patterns(version, published)
    drift: list[str] = []

    for rel in _tracked_files():
        if rel in _VERSION_PIN_EXEMPT:
            continue
        path = REPO_ROOT / rel
        try:
            data = path.read_bytes()
        except OSError:
            continue
        # Every pin shape contains "roam"; the structural sites all live in
        # files that do too. Cheap pre-filter so the sweep stays sub-second
        # over a few thousand tracked files.
        if b"roam" not in data:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue

        original = text
        for pat, repl in sweep + structural.get(rel, []):
            out: list[str] = []
            last = 0
            for m in pat.finditer(text):
                replaced = m.expand(repl)
                if replaced != m.group(0):
                    line_no = text.count("\n", 0, m.end()) + 1
                    drift.append(f"{rel}:{line_no}: {_trim_pin_context(m.group(0))} -> {_trim_pin_context(replaced)}")
                out.append(text[last : m.start()])
                out.append(replaced)
                last = m.end()
            out.append(text[last:])
            text = "".join(out)

        if write and text != original:
            path.write_text(text, encoding="utf-8", newline="")

    return drift


# Each entry is one of:
#   (path, [(pattern, replacement)...])              -- legacy whole-file path
#   (path, [(pattern, replacement)...], marker_aware) -- marker-masked path
# ``marker_aware=True`` runs substitution on a copy with the auto-count
# marker blocks masked out, so this script never rewrites bytes owned by
# the cousin ``dev/build_readme_counts.py``. Surfaces with no marker blocks
# use the 2-tuple legacy form. ``main()`` tolerates both.
REPLACEMENTS: list[tuple] = []


def _mcp_preset_description(preset_counts: dict[str, int]) -> str:
    """Render the server.json preset help from the complete runtime map."""
    parts = []
    for name, count in preset_counts.items():
        if name == "core":
            parts.append(f"core (default, {count} — lean prompt surface)")
        else:
            parts.append(f"{name} ({count})")
    return "Tool preset: " + ", ".join(parts)


def build_replacements(counts: dict, languages: int) -> None:
    """Build the (file, [(pattern, replacement)...], marker_aware) list."""
    REPLACEMENTS.clear()

    cmds = counts["commands"]
    canon = counts["canonical"]
    aliases = counts["alias_names"]
    mcp = counts["mcp_tools"]
    core = counts["mcp_core_tools"]
    preset_description = _mcp_preset_description(counts["mcp_preset_counts"])
    langs = languages

    # README.md — the auto-count MARKER blocks (headline / canonical-mention
    # / default-preset / tool-table) are owned by dev/build_readme_counts.py;
    # those entries below stay inert (repl=None). The FREE-FORM count phrases
    # OUTSIDE the markers — the MCP-section prose ("N tools, 10 resources"),
    # the directory-tree comments ("MCP server (N tools, ...)"), and the
    # "M canonical + K aliases" CLI annotation — had no guard at all and are
    # the surfaces this extension adds. marker_aware=True masks the cousin
    # script's territory so the two scripts cannot fight over bytes.
    REPLACEMENTS.append(
        (
            REPO_ROOT / "README.md",
            [
                # --- Inert: owned by dev/build_readme_counts.py marker blocks.
                (re.compile(r"\*\d+ commands · \d+ MCP tools · \d+ languages"), None),
                (re.compile(r"\bother \d+ specialised commands\b"), None),
                (re.compile(r"\bremaining ~\d+ commands\b"), None),
                (re.compile(r"canonical surface is \*\*\d+ commands"), None),
                # --- Active: free-form prose OUTSIDE the marker blocks.
                # "N tools, 10 resources, and 5 prompts are available in the full preset."
                (
                    re.compile(r"\b\d+ tools, (\d+ resources, and \d+ prompts are available)"),
                    rf"{mcp} tools, \1",
                ),
                # Directory-tree comment: "MCP server (N tools, 10 resources, 6 prompts)".
                (
                    re.compile(r"MCP server \(\d+ tools(, \d+ resources, \d+ prompts\))"),
                    rf"MCP server ({mcp} tools\1",
                ),
                # Directory-tree comment: "Click CLI (M canonical + K aliases)".
                (
                    re.compile(r"Click CLI \(\d+ canonical \+ \d+ aliases\)"),
                    f"Click CLI ({canon} canonical + {aliases} aliases)",
                ),
                # NOTE: the "are 90 of the N tools dead weight?" phrase in the
                # README MCP-tool table is NOT guarded here. It lives inside the
                # auto-count `readme-mcp-tool-list-table` marker block, sourced
                # verbatim from the `roam_session_metrics` docstring in
                # src/roam/mcp_server.py. Its `224` is stale vs the live 227,
                # but the fix belongs in mcp_server.py's docstring — the cousin
                # script regenerates the table from it. A pattern here could
                # only ever match inside the marker block (which marker-aware
                # masking skips), so it would be dead. Fix at the source.
            ],
            True,
        )
    )

    # CLAUDE.md — headline + authoritative blocks are marker-protected (owned
    # by build_readme_counts.py's claude-* blocks). The free-form architecture
    # prose ("N command names (M canonical + K aliases)", "57 tools in core
    # preset; up to N in full", "all N command names") had no guard.
    REPLACEMENTS.append(
        (
            REPO_ROOT / "CLAUDE.md",
            [
                (
                    re.compile(r"\b\d+ command names \(\d+ canonical \+ \d+ aliases\)"),
                    f"{cmds} command names ({canon} canonical + {aliases} aliases)",
                ),
                (
                    re.compile(r"FastMCP server \(\d+ tools in core preset; up to \d+ in `full`\)"),
                    f"FastMCP server ({core} tools in core preset; up to {mcp} in `full`)",
                ),
                (
                    re.compile(r"\bfor all \d+ command names\b"),
                    f"for all {cmds} command names",
                ),
            ],
            True,
        )
    )

    # AGENTS.md — same shape as CLAUDE.md. Codex-headline + Codex-authoritative
    # blocks are marker-protected; the free-form prose below is not.
    REPLACEMENTS.append(
        (
            REPO_ROOT / "AGENTS.md",
            [
                (
                    re.compile(r"\b\d+ command names \(\d+ canonical \+ \d+ aliases\)"),
                    f"{cmds} command names ({canon} canonical + {aliases} aliases)",
                ),
                (
                    re.compile(r"FastMCP server \(\d+ tools in core preset; \d+ in `full`\)"),
                    f"FastMCP server ({core} tools in core preset; {mcp} in `full`)",
                ),
                (
                    re.compile(r"\bfor all \d+ command names\b"),
                    f"for all {cmds} command names",
                ),
            ],
            True,
        )
    )

    # CONTRIBUTING.md — no marker blocks; one count-bearing reference-table row.
    REPLACEMENTS.append(
        (
            REPO_ROOT / "CONTRIBUTING.md",
            [
                (
                    re.compile(r"MCP server with \d+ tools \(\d+ in the default `core` preset\)"),
                    f"MCP server with {mcp} tools ({core} in the default `core` preset)",
                ),
            ],
            False,
        )
    )

    REPLACEMENTS.append(
        (
            REPO_ROOT / "llms-install.md",
            [
                # All None — see dev/build_readme_counts.py for the real writer.
                (re.compile(r"\b\d+ commands, \d+ MCP tools, \d+ languages\b"), None),
                (re.compile(r"all \d+ commands"), None),
            ],
        )
    )

    # server.json (language count + complete ROAM_MCP_PRESET description)
    REPLACEMENTS.append(
        (
            REPO_ROOT / "server.json",
            [
                (re.compile(r"\b\d+ languages\b"), f"{langs} languages"),
                (re.compile(r"Tool preset: [^\"]+"), preset_description),
            ],
        )
    )

    # cli.py top-of-file current-surface comment. The registry itself remains
    # the source of truth; this replacement keeps the human-facing summary
    # from silently lagging after command additions/removals.
    REPLACEMENTS.append(
        (
            REPO_ROOT / "src" / "roam" / "cli.py",
            [
                (
                    re.compile(
                        r"# Total: \d+ invokable command names "
                        r"\(\d+ canonical commands \+ \d+ alias names\)\."
                    ),
                    f"# Total: {cmds} invokable command names ({canon} canonical commands + {aliases} alias names).",
                ),
            ],
        )
    )

    # mcp-server-card.json — both copies
    # The second copy used to live at ``docs/site/.well-known/`` (served
    # via GitHub Pages at cranot.github.io). After GH Pages was disabled
    # on 2026-05-08, the canonical public copy moved under the
    # Cloudflare-served landing-page tree so the card_url claim
    # (``roam-code.com/.well-known/mcp-server-card.json``) keeps working.
    for p in [
        REPO_ROOT / "src" / "roam" / "mcp-server-card.json",
        REPO_ROOT / "templates" / "distribution" / "landing-page" / ".well-known" / "mcp-server-card.json",
    ]:
        REPLACEMENTS.append(
            (
                p,
                [
                    (re.compile(r'"total":\s*\d+,?(\s*\n\s*"watched")'), None),  # don't touch resources count
                ],
            )
        )

    # ----- Public landing page + docs site -----
    # Reviewer (2026-05-08) found 5 different command counts on
    # different surfaces because these files weren't in the script.
    # All of them must match the live counts.

    # Cardinal pattern across the landing-page HTML files: any standalone
    # ``N CLI commands``, ``N commands``, ``N MCP tools``, or
    # ``N languages``. The regex deliberately uses word boundaries so we
    # don't catch e.g. "v12.50" or unrelated numerics.
    # Marketing-tone pages where the user has chosen the soft-count
    # framing ("200+ CLI capabilities", "130+ MCP tools", "28 language
    # families") deliberately, per the strategic-reframe directive on
    # 2026-05-09. These pages are EXCLUDED from auto-sync; otherwise
    # the script would clobber the soft framing with hard counts and
    # contradict the positioning. Reference / docs / press surfaces
    # below still get hard counts.
    SOFT_COUNT_PAGES = {
        REPO_ROOT / "templates" / "distribution" / "landing-page" / "index.html",
    }

    landing_pages = [
        REPO_ROOT / "templates" / "distribution" / "landing-page" / "index.html",
        REPO_ROOT / "templates" / "distribution" / "landing-page" / "setup.html",
        REPO_ROOT / "templates" / "distribution" / "landing-page" / "pricing.html",
        REPO_ROOT / "templates" / "distribution" / "landing-page" / "compare.html",
        REPO_ROOT / "templates" / "distribution" / "landing-page" / "press.html",
        REPO_ROOT / "templates" / "distribution" / "landing-page" / "llms.txt",
        REPO_ROOT / "templates" / "distribution" / "landing-page" / "docs" / "index.html",
        REPO_ROOT / "templates" / "distribution" / "landing-page" / "docs" / "command-reference.html",
        REPO_ROOT / "templates" / "distribution" / "landing-page" / "docs" / "getting-started.html",
        # Added 2026-05-21: docs pages that quote the same hard counts but
        # were never walked by this script — the gap the 224-vs-227 drift
        # cascade exposed. mcp-usage.html also gets the cardinal patterns
        # here (the explicit ``exposes all N`` pin below is additive).
        REPO_ROOT / "templates" / "distribution" / "landing-page" / "docs" / "mcp-usage.html",
        REPO_ROOT / "templates" / "distribution" / "landing-page" / "docs" / "integration-tutorials.html",
        REPO_ROOT / "templates" / "distribution" / "landing-page" / "docs" / "canonical-demo.html",
    ]
    for p in landing_pages:
        if p in SOFT_COUNT_PAGES:
            continue
        REPLACEMENTS.append(
            (
                p,
                [
                    (re.compile(r"\b\d+ CLI commands\b"), f"{cmds} CLI commands"),
                    (re.compile(r"\b\d+ commands\b"), f"{cmds} commands"),
                    (re.compile(r"\b\d+ MCP tools\b"), f"{mcp} MCP tools"),
                    (re.compile(r"\((\d+) tools\)"), f"({mcp} tools)"),
                    (re.compile(r"\bRoam's \d+ tools\b"), f"Roam's {mcp} tools"),
                    (re.compile(r"\b\d+ languages\b"), f"{langs} languages"),
                ],
            )
        )

    # Explicit MCP preset counts on the command-reference / MCP usage pages.
    REPLACEMENTS.append(
        (
            REPO_ROOT / "templates" / "distribution" / "landing-page" / "docs" / "command-reference.html",
            [
                (re.compile(r"all (\d+) MCP tools"), f"all {mcp} MCP tools"),
                (re.compile(r"All (\d+) commands"), f"All {cmds} commands"),
                (
                    re.compile(
                        r"default: \d+ (?:core tools(?: plus the <code>roam_expand_toolset</code> meta-tool)?|tools including the <code>roam_expand_toolset</code> meta-tool); \d+ in <code>full</code>"
                    ),
                    f"default: {core} tools including the <code>roam_expand_toolset</code> meta-tool; {mcp} in <code>full</code>",
                ),
            ],
        )
    )
    REPLACEMENTS.append(
        (
            REPO_ROOT / "templates" / "distribution" / "landing-page" / "docs" / "mcp-usage.html",
            [
                (re.compile(r"exposes all\s+\d+(?: tools)?\."), f"exposes all\n        {mcp} tools."),
            ],
        )
    )

    # agent-contract.html — the "Surface scale" count table has one labeled
    # row per count, so each substitution is anchored on the row label
    # (``<td>LABEL</td><td>N</td>``). Anchoring on the label keeps the
    # 1-row ("Canonical envelope" -> 1) untouched.
    REPLACEMENTS.append(
        (
            REPO_ROOT / "templates" / "distribution" / "landing-page" / "docs" / "agent-contract.html",
            [
                (
                    re.compile(r"(<td>CLI commands</td><td>)\d+(</td>)"),
                    rf"\g<1>{cmds}\g<2>",
                ),
                (
                    re.compile(r"(<td>MCP tools registered</td><td>)\d+(</td>)"),
                    rf"\g<1>{mcp}\g<2>",
                ),
                (
                    re.compile(r"(<td>MCP tools in <code>core</code> preset</td><td>)\d+(</td>)"),
                    rf"\g<1>{core}\g<2>",
                ),
                (
                    re.compile(r"(<td>Languages</td><td>)\d+(</td>)"),
                    rf"\g<1>{langs}\g<2>",
                ),
                # "234 canonical + 7 aliases." note cell + "all 227 tools" prose.
                (
                    re.compile(r"\b\d+ canonical \+ \d+ aliases\b"),
                    f"{canon} canonical + {aliases} aliases",
                ),
                (
                    re.compile(r"\ball \d+ tools\b"),
                    f"all {mcp} tools",
                ),
            ],
        )
    )

    # ``docs/site/data/landscape.json`` was deleted on 2026-05-08 when
    # GitHub Pages was disabled. The competitor data still lives in
    # ``src/roam/competitor_site_data.py`` (Python module) and the
    # gitignored ``internal/competitor_tracker.md`` (source of truth).

    # src/roam/competitor_site_data.py — peer-entry self-reference.
    REPLACEMENTS.append(
        (
            REPO_ROOT / "src" / "roam" / "competitor_site_data.py",
            [
                (re.compile(r"\b\d+ MCP tools, \d+ CLI commands\b"), f"{mcp} MCP tools, {cmds} CLI commands"),
            ],
        )
    )

    # skills/roam/SKILL.md — Claude Code skill mentions the count.
    REPLACEMENTS.append(
        (
            REPO_ROOT / "skills" / "roam" / "SKILL.md",
            [
                (re.compile(r"\broam has \d+ commands\b"), f"roam has {cmds} commands"),
            ],
        )
    )

    # docs/ci-integration.md — "all N commands" footer.
    REPLACEMENTS.append(
        (
            REPO_ROOT / "docs" / "ci-integration.md",
            [
                (re.compile(r"all \d+ commands"), f"all {cmds} commands"),
            ],
        )
    )


def _scrape(text: str, pat: re.Pattern) -> str | None:
    m = pat.search(text)
    return m.group(0) if m else None


def iter_replacements() -> "list[tuple[Path, list, bool]]":
    """Yield every REPLACEMENTS entry normalised to ``(path, patterns, marker_aware)``.

    ``build_replacements`` must have been called first. Entries are stored
    as either 2-tuples (legacy whole-file) or 3-tuples (marker-aware); this
    helper hides that distinction so callers — including tests — iterate one
    stable shape. Adding a new ``marker_aware`` surface no longer breaks any
    consumer that unpacks the list.
    """
    out: list[tuple[Path, list, bool]] = []
    for entry in REPLACEMENTS:
        if len(entry) == 3:
            path, patterns, marker_aware = entry
        else:
            path, patterns = entry
            marker_aware = False
        out.append((path, patterns, marker_aware))
    return out


# Files whose count phrases live OUTSIDE the auto-count marker blocks owned by
# ``dev/build_readme_counts.py``. The cousin script writes the marker-protected
# headline / authoritative blocks; this script owns the free-form prose count
# phrases scattered through the rest of the same Markdown files (directory-tree
# comments, MCP-section prose, the contributor reference table). To keep the
# two scripts strictly non-overlapping, every substitution below is applied to
# a *marker-masked* copy of the text — see ``_apply_marker_aware``.
_MARKER_BLOCK = re.compile(
    r"<!--\s*BEGIN auto-count:.*?-->.*?<!--\s*END auto-count:.*?-->",
    flags=re.DOTALL,
)


def _marker_spans(text: str) -> list[tuple[int, int]]:
    """Return (start, end) char spans of every auto-count marker block."""
    return [(m.start(), m.end()) for m in _MARKER_BLOCK.finditer(text)]


def _apply_marker_aware(text: str, patterns: list[tuple[re.Pattern, str | None]]) -> tuple[str, list[tuple[str, str]]]:
    """Apply (pattern, replacement) substitutions OUTSIDE marker blocks only.

    Returns ``(new_text, hits)`` where ``hits`` is a list of
    ``(before, after)`` pairs for reporting. Substitutions whose only
    match falls inside an auto-count marker block are skipped — those
    sites are owned by ``dev/build_readme_counts.py`` and rewriting them
    here would make the two scripts fight over the same bytes.
    """
    spans = _marker_spans(text)

    def _in_marker(pos: int) -> bool:
        return any(start <= pos < end for start, end in spans)

    hits: list[tuple[str, str]] = []
    for pat, repl in patterns:
        if repl is None:
            continue
        # Re-scan from scratch each iteration so positions stay valid.
        out: list[str] = []
        last = 0
        spans = _marker_spans(text)
        for m in pat.finditer(text):
            if _in_marker(m.start()):
                continue
            out.append(text[last : m.start()])
            replaced = m.expand(repl)
            out.append(replaced)
            if replaced != m.group(0):
                hits.append((m.group(0), replaced))
            last = m.end()
        out.append(text[last:])
        text = "".join(out)
    return text, hits


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help="Rewrite files in place (default: dry-run)")
    args = ap.parse_args()

    version = _pyproject_version()
    published = _published_version()
    counts = _live_counts()
    langs = _live_languages()
    # ``with aliases`` reuses live alias_names from surface_counts rather than
    # a literal "+ 7" magic number — the moment a new alias lands in
    # ``cli._COMMANDS``, the header reflects it without an edit here
    # (W933-class stale-literal hazard).
    with_aliases = counts["canonical"] + counts["alias_names"]
    print(f"Live surface: {counts['commands']} commands ({counts['canonical']} canonical, {with_aliases} with aliases)")
    print(f"               {counts['mcp_tools']} MCP tools, {langs} languages")
    print(f"Declared version (pyproject.toml): {version}   -> identity pins")
    print(f"Published release (highest v* tag): {published}   -> install pins")
    if version != published:
        print(f"  bump-to-release window is OPEN: install pins stay at {published} until v{version} is tagged")
    print()

    build_replacements(counts, langs)

    drift_found = 0
    for path, patterns, marker_aware in iter_replacements():
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            print(f"WARN: cannot read {path.relative_to(REPO_ROOT)}: {e}")
            continue
        original = text
        rel = path.relative_to(REPO_ROOT).as_posix()
        if marker_aware:
            # Substitute only OUTSIDE auto-count marker blocks — those are
            # owned by dev/build_readme_counts.py.
            text, hits = _apply_marker_aware(text, patterns)
            for before, after in hits:
                drift_found += 1
                print(f"  {rel}: '{before}' -> '{after}'")
        else:
            for pat, repl in patterns:
                if repl is None:
                    continue
                new_text = pat.sub(repl, text)
                if new_text != text:
                    drift_found += 1
                    # Show before / after of the first match
                    m_before = pat.search(text)
                    if m_before:
                        print(f"  {rel}: '{m_before.group(0)}' -> '{repl}'")
                    text = new_text
        if args.write and text != original:
            path.write_text(text, encoding="utf-8")
            print(f"  -> wrote {rel}")

    # W1501 — release-version pins. Separate pass from the count REPLACEMENTS
    # above: the pin sweep walks every TRACKED file (so a new surface is
    # covered the day it lands), while the count machinery is an explicit
    # per-file registry whose overlap with dev/build_readme_counts.py is
    # pinned by tests/test_count_drift_no_overlap.py.
    pin_drift = release_pin_drift(version, published, write=args.write)
    for line in pin_drift:
        print(f"  {line}")
    drift_found += len(pin_drift)

    print()
    if drift_found == 0:
        print("All surface counts in sync.")
        print(f"All identity pins match pyproject {version}; all install pins match published {published}.")
        return 0
    if args.write:
        print(f"Synced {drift_found} pattern(s).")
        return 0
    print(f"{drift_found} drifted pattern(s). Run with --write to fix.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
