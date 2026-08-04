"""W1501 — every release-version pin agrees with ``pyproject.toml``.

CONTRIBUTING claimed since v11 that ``pyproject.toml -> version`` was the
single source of truth and that "everything else syncs from it via
``scripts/sync_surface_counts.py``". That script synced surface COUNTS and
contained no version handling at all, so the claim was never true and nothing
ever failed on it. The consequence is concrete: a release that bumps only
``pyproject.toml`` ships ``action.yml`` with ``default: '<previous version>'``,
so every downstream consumer of the composite action installs the version
BEFORE the one just released, and ``server.json`` tells the MCP registry to
fetch the same stale wheel.

This module pins the mechanism that closes it. Three things are asserted:

* **positive control** — the real tree has no pin drift;
* **negative control** — the gate REPORTS drift when a derived pin disagrees,
  proven against a synthetic tree rather than asserted. A gate never shown to
  fail is indistinguishable from one that does not work, which is exactly how
  the CONTRIBUTING claim survived;
* **false-positive controls** — version-shaped literals that are NOT roam
  release pins (a CircleCI config version, a pinned third-party action
  version, prose naming a past release) must survive the sweep untouched.

Also pinned here: the *classification* itself. The ``13.10.0`` occurrences in
``roam/plan/compiler.py`` and ``roam/plan/plan_cache.py`` are prose in
comments describing a measured cross-version cache collision — the runtime
version stamp is read from the installed ``dist-info`` directory name, never
from a literal. ``test_plan_cache_version_literals_are_comments_only`` proves
that with the tokenizer so a future edit that promotes one of those literals
into code (where bumping it would silently change cache-key behaviour) fails
here instead of shipping.
"""

from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

import pytest

from scripts import sync_surface_counts as sync
from tests._helpers.repo_root import repo_root

ROOT = repo_root()


# ---------------------------------------------------------------------------
# Source of truth
# ---------------------------------------------------------------------------


def test_pyproject_version_is_readable_and_release_shaped() -> None:
    version = sync._pyproject_version()
    assert re.fullmatch(r"\d+\.\d+(\.\d+)?", version), f"unexpected version shape: {version!r}"


# ---------------------------------------------------------------------------
# Positive control — the real tree
# ---------------------------------------------------------------------------


def test_tracked_tree_has_no_release_pin_drift() -> None:
    """Every derived pin in the checkout equals ``pyproject.toml``.

    This is the gate CI enforces via the ``Surface-count drift gate`` step
    (``.venv/bin/python scripts/sync_surface_counts.py``); asserting it here
    too means a stale pin fails the ordinary test run as well, not only the
    doc-hygiene job.
    """
    version = sync._pyproject_version()
    drift = sync.release_pin_drift(version)
    assert drift == [], "release-version pins disagree with pyproject:\n  " + "\n  ".join(drift)


# ---------------------------------------------------------------------------
# Negative control — the gate must fail when a derived pin disagrees
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A synthetic repo the sweep treats exactly like the real one.

    ``release_pin_drift`` resolves paths against the module-level
    ``REPO_ROOT`` and enumerates candidates through ``_tracked_files``; both
    are redirected so the negative control exercises the real matching engine
    rather than a re-implementation of it.
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


_STALE_TEMPLATE = 'python -m pip install --quiet "roam-code==13.9.0"\n'
_STALE_WORKFLOW = "      - uses: Cranot/roam-code@v13.9.0\n        with:\n          version: '13.9.0'\n"
_STALE_ACTION = (
    "inputs:\n"
    "  version:\n"
    "    description: 'Exact roam-code version to install'\n"
    "    required: false\n"
    "    default: '13.9.0'\n"
)
_STALE_SERVER_JSON = (
    "{\n"
    '    "name": "io.github.cranot/roam-code",\n'
    '    "version": "13.9.0",\n'
    '    "packages": [\n'
    "        {\n"
    '            "registryType": "pypi",\n'
    '            "identifier": "roam-code",\n'
    '            "version": "13.9.0"\n'
    "        }\n"
    "    ]\n"
    "}\n"
)


def test_gate_reports_every_stale_derived_pin(fake_tree) -> None:
    """The negative control: each derived shape is CAUGHT, and named."""
    fake_tree(
        {
            "src/roam/templates/ci/gitlab-ci.yml": _STALE_TEMPLATE,
            "docs/ci-integration.md": _STALE_WORKFLOW,
            "action.yml": _STALE_ACTION,
            "server.json": _STALE_SERVER_JSON,
        }
    )
    drift = sync.release_pin_drift("14.0.0")
    reported = {line.split(":", 1)[0] for line in drift}
    assert reported == {
        "src/roam/templates/ci/gitlab-ci.yml",
        "docs/ci-integration.md",
        "action.yml",
        "server.json",
    }, drift
    # The action input default and the registry package pin are the two sites
    # that silently mis-serve the previous release to machines; both must be
    # in the report, not just the human-readable docs.
    assert any("action.yml" in line and "13.9.0" in line and "14.0.0" in line for line in drift), drift
    assert sum(1 for line in drift if line.startswith("server.json")) == 2, drift


def test_write_mode_repairs_every_reported_site(fake_tree) -> None:
    """``--write`` must actually fix what the check reports.

    A checker whose autofix does not converge is worse than no autofix: the
    release runs the command, sees "synced", and ships the stale pin anyway.
    """
    tree = fake_tree(
        {
            "src/roam/templates/ci/gitlab-ci.yml": _STALE_TEMPLATE,
            "action.yml": _STALE_ACTION,
            "server.json": _STALE_SERVER_JSON,
        }
    )
    assert sync.release_pin_drift("14.0.0", write=True) != []
    assert sync.release_pin_drift("14.0.0") == []
    assert '"roam-code==14.0.0"' in (tree / "src/roam/templates/ci/gitlab-ci.yml").read_text(encoding="utf-8")
    assert "default: '14.0.0'" in (tree / "action.yml").read_text(encoding="utf-8")
    assert (tree / "server.json").read_text(encoding="utf-8").count('"14.0.0"') == 2


def test_exempt_paths_are_not_rewritten(fake_tree) -> None:
    """Historical and deliberately-lagging files survive a bump untouched."""
    exempt = sorted(sync._VERSION_PIN_EXEMPT)
    assert exempt, "the exemption registry must not be empty — see the module docstring"
    tree = fake_tree({rel: _STALE_TEMPLATE + _STALE_WORKFLOW for rel in exempt})
    assert sync.release_pin_drift("14.0.0", write=True) == []
    for rel in exempt:
        assert "13.9.0" in (tree / rel).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# False-positive controls — these are the assertions that make the gate safe
# to run with --write in a release
# ---------------------------------------------------------------------------


_NOT_A_ROAM_PIN = {
    # CircleCI config schema version, and a pinned third-party action version.
    # A naive `^\s*version:` pattern rewrites both.
    "templates/examples/roam-guard-pr.circleci.yml": ("version: 2.1\njobs:\n  roam-guard:\n    docker: []\n"),
    ".github/workflows/example.yml": (
        "      - uses: astral-sh/setup-uv@11f9893b081a58869d3b5fccaea48c9e9e46f990\n"
        "        with:\n"
        '          version: "0.11.29"\n'
    ),
    # Prose that NAMES a past release. Rewriting it falsifies a record.
    "CHANGELOG.md": (
        "- Measured against the shipped 13.10.0 binary, roam verdict returned\n"
        "  exit 0. See the compare link v13.9.0...v13.10.0 for the full diff.\n"
    ),
    # A recorded measurement.
    "benchmarks/cross-repo-l1/RESULTS.md": ("Engine: roam-code `b6a8e87f` (`roam, version 13.10.0`)\n"),
    # An illustrative grammar example inside an error message.
    "action.yml.snippet": ('fail_input "version" "expected a closed PEP 440-style release such as 13.10.0"\n'),
}


@pytest.mark.parametrize("rel", sorted(_NOT_A_ROAM_PIN))
def test_non_pin_version_literals_are_never_rewritten(fake_tree, rel: str) -> None:
    tree = fake_tree({rel: _NOT_A_ROAM_PIN[rel]})
    assert sync.release_pin_drift("14.0.0", write=True) == [], rel
    assert (tree / rel).read_text(encoding="utf-8") == _NOT_A_ROAM_PIN[rel]


# ---------------------------------------------------------------------------
# Registry hygiene + classification proofs
# ---------------------------------------------------------------------------


def test_every_exemption_still_points_at_a_real_file() -> None:
    """A stale exemption is drift too — it silently un-guards nothing while
    reading as if it guards something."""
    missing = [rel for rel in sync._VERSION_PIN_EXEMPT if not (ROOT / rel).exists()]
    assert not missing, f"exemptions naming files that no longer exist: {missing}"


def test_every_exemption_carries_a_reason() -> None:
    empty = [rel for rel, why in sync._VERSION_PIN_EXEMPT.items() if len(why.strip()) < 20]
    assert not empty, f"exemptions without a substantive reason: {empty}"


@pytest.mark.parametrize(
    "rel",
    ["src/roam/plan/compiler.py", "src/roam/plan/plan_cache.py"],
)
def test_plan_cache_version_literals_are_comments_only(rel: str) -> None:
    """The compile-cache version stamp is READ, never written as a literal.

    ``plan_cache._roam_version_stamp`` derives the stamp from the installed
    ``roam_code-<version>.dist-info`` directory name, so the version numbers
    appearing in these two modules are narrative in comments. If one ever
    becomes a string or numeric token, a version bump would change cache-key
    behaviour — a functional change wearing a version sync's clothes — and
    this assertion is what makes that visible.
    """
    source = (ROOT / rel).read_text(encoding="utf-8")
    current = sync._pyproject_version()
    # Match the CURRENT release and the two-segment family it belongs to, so
    # the assertion tracks the version actually being shipped rather than a
    # literal that would itself go stale.
    family = re.escape(current.rsplit(".", 1)[0])
    pattern = re.compile(rf"\b(?:{re.escape(current)}|{family}\.\d+)\b")
    offenders = [
        (tok.start[0], tok.string[:60])
        for tok in tokenize.generate_tokens(io.StringIO(source).readline)
        if tok.type != tokenize.COMMENT and pattern.search(tok.string)
    ]
    assert offenders == [], (
        f"{rel} carries a roam release version outside a comment: {offenders}. "
        "The compile cache derives its version stamp from dist-info at runtime; "
        "a literal here would make a version bump a behaviour change, not a doc sync."
    )
