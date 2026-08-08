"""W1501 — every release-version pin agrees with its CLASS's source.

CONTRIBUTING claimed since v11 that ``pyproject.toml -> version`` was the
single source of truth and that "everything else syncs from it via
``scripts/sync_surface_counts.py``". That script synced surface COUNTS and
contained no version handling at all, so the claim was never true and nothing
ever failed on it. The consequence is concrete: a release that bumps only
``pyproject.toml`` ships ``action.yml`` with ``default: '<previous version>'``,
so every downstream consumer of the composite action installs the version
BEFORE the one just released, and ``server.json`` tells the MCP registry to
fetch the same stale wheel.

The first fix made pyproject the single source for ALL of them, which closed
that defect and opened its mirror image. ``pyproject.toml`` is the DECLARED
version; a version is only installable once it is PUBLISHED. Syncing install
instructions forward at BUMP time therefore guaranteed that public ``main``
advertised a version nobody could fetch, for the entire bump-to-release
window — measured at 33 commits and counting when 14.0.0 was declared against
a latest-published 13.10.0, with ``pip install "roam-code==14.0.0"`` returning
404 and ``Cranot/roam-code@v14.0.0`` resolving to no ref. The gate was green
throughout, because it was asking whether the pins agreed with pyproject
rather than whether what they named existed.

So there are two sources, not one:

* **identity** literals (``CITATION.cff``, ``codemeta.json``, the plugin
  manifest, ``server.json``'s own version, generated doc headers) describe
  the artifact this commit IS -> ``pyproject.toml``;
* **install** literals (``roam-code==X``, ``Cranot/roam-code@vX``, the
  action's ``version`` input and default, ``server.json``'s
  ``packages[].version``, the roam-guard ``actual == 'X'`` assertion) instruct
  a fetch -> the last PUBLISHED release, i.e. the highest ``v*`` tag.

This module pins the mechanism. Five things are asserted:

* **positive control** — the real tree has no pin drift;
* **negative control** — the gate REPORTS drift when a derived pin disagrees,
  proven against a synthetic tree rather than asserted. A gate never shown to
  fail is indistinguishable from one that does not work, which is exactly how
  the CONTRIBUTING claim survived;
* **the class split** — with declared and published DELIBERATELY different,
  install pins land on published and identity pins land on declared. Every
  other test in this file passes the two versions equal, which is the steady
  state after a release and cannot tell the two sources apart;
* **fail-closed** — an empty or unreachable tag list makes the published
  version UNKNOWN, and UNKNOWN refuses rather than falling back to declared.
  A fallback there would restore the whole defect in exactly the place it is
  least visible: a shallow clone or a fresh fork;
* **false-positive controls** — version-shaped literals that are NOT roam
  release pins (a CircleCI config version, a pinned third-party action
  version, prose naming a past release) must survive the sweep untouched.

What this module does NOT check: whether the version an install pin names can
actually be FETCHED. The tag proves the GitHub half only, and it proves it for
this repository's own tag list rather than for PyPI.
``scripts/check_install_targets.py`` is the gate for that question.

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
    """Every pin in the checkout equals the source its CLASS is derived from.

    Identity pins equal ``pyproject.toml``; install pins equal the last
    published release. This is the gate CI enforces via the ``Surface-count
    drift gate`` step (``.venv/bin/python scripts/sync_surface_counts.py``);
    asserting it here too means a stale pin fails the ordinary test run as
    well, not only the doc-hygiene job.
    """
    version = sync._pyproject_version()
    published = sync._published_version()
    drift = sync.release_pin_drift(version, published)
    assert drift == [], "release-version pins disagree with their class source:\n  " + "\n  ".join(drift)


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
    drift = sync.release_pin_drift("14.0.0", "14.0.0")
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


# ---------------------------------------------------------------------------
# The class split — install pins vs identity pins
# ---------------------------------------------------------------------------
#
# These are the assertions that make the two classes distinguishable. Every
# test above passes ``version == published``, which is the steady state after
# a release and therefore cannot tell the two sources apart. The whole defect
# lived in the window where they DIFFER.


# The literal "roam" is load-bearing in these fixtures, not decoration: the
# sweep pre-filters on it to stay sub-second over a few thousand tracked files,
# so a fixture without it is skipped and the assertion silently measures
# nothing. Both real files contain it.
_IDENTITY_CITATION = "cff-version: 1.2.0\ntitle: roam-code\nversion: 13.9.0\n"
_IDENTITY_PLUGIN = '{\n    "name": "roam",\n    "version": "13.9.0"\n}\n'


def test_install_pins_take_published_and_identity_pins_take_declared(fake_tree) -> None:
    """The bump-to-release window, reproduced: declared 14.0.0, published 13.10.0.

    This is the exact state public ``main`` was in. Before the split, every
    site below was rewritten to 14.0.0 and the gate printed "in sync" — while
    ``pip install "roam-code==14.0.0"`` returned 404 from PyPI and
    ``Cranot/roam-code@v14.0.0`` resolved to no ref at all. Both halves are
    asserted here: install literals must land on the PUBLISHED release, and
    identity literals must still land on the DECLARED one. Asserting only the
    first would let a fix that froze the whole tree at 13.10.0 pass.
    """
    tree = fake_tree(
        {
            # install class
            "src/roam/templates/ci/gitlab-ci.yml": _STALE_TEMPLATE,
            "action.yml": _STALE_ACTION,
            "server.json": _STALE_SERVER_JSON,
            "templates/examples/roam-guard-pr.github-actions.yml": (
                "pip install \"roam-code==13.9.0\"\nraise SystemExit(0 if actual == '13.9.0' else 1)\n"
            ),
            "docs/ci-integration.md": _STALE_WORKFLOW + "| `version` | `13.9.0` |\n",
            # identity class
            "CITATION.cff": _IDENTITY_CITATION,
            ".claude-plugin/plugin.json": _IDENTITY_PLUGIN,
        }
    )
    assert sync.release_pin_drift("14.0.0", "13.10.0", write=True) != []
    assert sync.release_pin_drift("14.0.0", "13.10.0") == [], "the rewrite must converge in one pass"

    def body(rel: str) -> str:
        return (tree / rel).read_text(encoding="utf-8")

    # INSTALL — every one of these instructs a fetch, so it names the release
    # that exists, not the one that was declared.
    assert '"roam-code==13.10.0"' in body("src/roam/templates/ci/gitlab-ci.yml")
    assert "default: '13.10.0'" in body("action.yml")
    assert body("server.json").count('"13.10.0"') == 1, "only packages[].version is install-class"
    assert body("server.json").count('"14.0.0"') == 1, "server.json's own version is identity-class"
    guard = body("templates/examples/roam-guard-pr.github-actions.yml")
    assert '"roam-code==13.10.0"' in guard
    assert "actual == '13.10.0'" in guard, "the post-install assertion must agree with the pin above it"
    doc = body("docs/ci-integration.md")
    assert "Cranot/roam-code@v13.10.0" in doc
    assert "version: '13.10.0'" in doc
    assert "| `version` | `13.10.0` |" in doc
    assert "14.0.0" not in doc, "no install instruction may name an unpublished version"

    # IDENTITY — these describe the artifact this commit IS, so they move at
    # bump time. Freezing them would be the mirror-image defect.
    assert "version: 14.0.0" in body("CITATION.cff")
    assert '"version": "14.0.0"' in body(".claude-plugin/plugin.json")


def test_published_version_refuses_when_no_release_tag_exists(monkeypatch) -> None:
    """UNKNOWN refuses; it never falls back to the declared version.

    A fallback here would silently restore the whole defect — and would do so
    exactly where it is least visible, in a shallow clone or a fresh fork
    where the tag list is empty rather than wrong.
    """
    monkeypatch.setattr(sync, "release_tags", lambda: [])
    with pytest.raises(SystemExit) as exc:
        sync._published_version()
    assert "UNKNOWN" in str(exc.value)
    assert "fetch-depth" in str(exc.value), "the message must say how to make the tag list reachable"


def test_a_tag_only_this_checkout_has_is_not_published(monkeypatch) -> None:
    """The release blocker: a local tag is published to nobody.

    ``git tag v14.0.0`` used to make this module believe 14.0.0 was PUBLISHED,
    so it demanded all 48 install pins move to a version no consumer could
    fetch — and refused the very ``git push origin v14.0.0`` that would have
    made the tag real. The sweep blocked the release it exists to protect, and
    complying with it would have put 404ing pins on public main. Measured
    2026-08-08 with a two-arm control: tag present -> rc 1, tag absent -> rc 0,
    nothing else changed.
    """
    monkeypatch.setattr(sync, "release_tags", lambda: ["v13.10.0", "v14.0.0"])
    monkeypatch.setattr(sync, "_remote_release_tags", lambda: ["v13.10.0"])
    assert sync._published_version() == "13.10.0", (
        "a tag the remote does not have must not count as the published release"
    )
    assert sync._PUBLISHED_SOURCE == "remote tags"


def test_an_unreachable_remote_degrades_loudly_rather_than_silently(monkeypatch) -> None:
    """Offline is allowed to answer, but not allowed to sound verified.

    Refusing outright would break every offline run; answering from local tags
    without saying so would republish the defect above under a clean-looking
    report. The answer is given AND labelled.
    """
    monkeypatch.setattr(sync, "release_tags", lambda: ["v13.10.0", "v14.0.0"])
    monkeypatch.setattr(sync, "_remote_release_tags", lambda: None)
    assert sync._published_version() == "14.0.0"
    assert "UNVERIFIED" in sync._PUBLISHED_SOURCE, (
        "a degraded answer must be labelled, or it is indistinguishable from a verified one"
    )


def test_a_remote_with_no_release_tag_refuses(monkeypatch) -> None:
    """Nothing published is UNKNOWN, not "use whatever is local"."""
    monkeypatch.setattr(sync, "release_tags", lambda: ["v14.0.0"])
    monkeypatch.setattr(sync, "_remote_release_tags", lambda: [])
    with pytest.raises(SystemExit) as exc:
        sync._published_version()
    assert "published to nobody" in str(exc.value)


def test_remote_release_tags_survives_annotated_peel_refs_and_junk(monkeypatch) -> None:
    """``ls-remote`` lists annotated tags twice; the peel suffix is not a tag."""
    import subprocess

    payload = (
        b"aaaa\trefs/tags/v13.10.0\nbbbb\trefs/tags/v13.10.0^{}\ncccc\trefs/tags/v14.0.0-rc1\ndddd\trefs/heads/main\n"
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0] if a else [], 0, payload, b""),
    )
    assert sync._remote_release_tags() == ["v13.10.0", "v13.10.0"]


def test_remote_release_tags_returns_none_when_the_remote_cannot_answer(monkeypatch) -> None:
    """None means "could not ask", which is not the same as "no tags"."""
    import subprocess

    def _boom(*a: object, **k: object):
        raise OSError("no network")

    monkeypatch.setattr(subprocess, "run", _boom)
    assert sync._remote_release_tags() is None


def test_release_tags_refuses_when_git_is_unreachable(monkeypatch) -> None:
    """An unreachable tag list is not an empty tag list, and neither is OK."""
    import subprocess

    def _fail(*_a, **_kw):
        return subprocess.CompletedProcess([], returncode=128, stdout=b"", stderr=b"not a git repository")

    monkeypatch.setattr(subprocess, "run", _fail)
    with pytest.raises(SystemExit) as exc:
        sync.release_tags()
    assert "git" in str(exc.value)


def test_release_tags_ignores_non_release_tags(monkeypatch) -> None:
    """Pre-releases and vanity tags must not be mistaken for a publish.

    ``_published_version`` picks the MAXIMUM, so a stray ``v99-wip`` tag would
    otherwise become the install target for the entire tree.
    """
    import subprocess

    out = b"v13.9.0\nv13.10.0\nv14.0.0rc1\nv99-wip\nvnext\n"
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_a, **_kw: subprocess.CompletedProcess([], returncode=0, stdout=out, stderr=b""),
    )
    # The property under test is TAG FILTERING, not remote resolution. Since
    # `_published_version` began asking the remote which tags are actually
    # published, it too calls `subprocess.run` — and would read this
    # `git tag --list` payload as `ls-remote` output, find no `refs/tags/`
    # line, and correctly refuse. Pin the remote to the same published set so
    # this test keeps measuring the one thing it is named for.
    monkeypatch.setattr(sync, "_remote_release_tags", lambda: ["v13.9.0", "v13.10.0"])
    assert sync.release_tags() == ["v13.9.0", "v13.10.0"]
    assert sync._published_version() == "13.10.0", "13.10.0 > 13.9.0 numerically, not lexically"


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
    assert sync.release_pin_drift("14.0.0", "14.0.0", write=True) != []
    assert sync.release_pin_drift("14.0.0", "14.0.0") == []
    assert '"roam-code==14.0.0"' in (tree / "src/roam/templates/ci/gitlab-ci.yml").read_text(encoding="utf-8")
    assert "default: '14.0.0'" in (tree / "action.yml").read_text(encoding="utf-8")
    assert (tree / "server.json").read_text(encoding="utf-8").count('"14.0.0"') == 2


def test_exempt_paths_are_not_rewritten(fake_tree) -> None:
    """Historical and deliberately-lagging files survive a bump untouched."""
    exempt = sorted(sync._VERSION_PIN_EXEMPT)
    assert exempt, "the exemption registry must not be empty — see the module docstring"
    tree = fake_tree({rel: _STALE_TEMPLATE + _STALE_WORKFLOW for rel in exempt})
    assert sync.release_pin_drift("14.0.0", "14.0.0", write=True) == []
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
    assert sync.release_pin_drift("14.0.0", "14.0.0", write=True) == [], rel
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
