"""Regression tests for ``roam stale-refs --fix`` corruption bugs.

Two CRITICAL false-positives motivated this file (found via external
dogfood feedback, May 2026):

* **Bug 1 — display-string-as-path.** A hyperlink shaped
  ``[`code-map/X.md`](../code-map/X.md)`` had its display half
  (``code-map/X.md`` inside the backticks) resolved as a filesystem
  path. The URL half was always live; the display half was never
  intended as a claim about disk layout. The ``--fix`` pipeline then
  proposed prefix-rewriting the URL ("looks like it needs
  ``docs/legacy/``") which CORRUPTED a working link into a
  double-prefix-broken one.

* **Bug 2 — bare-backtick prose noise.** Sentences like
  ``Look at the `MyDataController.php` for details.`` had the
  basename treated as a filesystem reference. Backticks are inline
  code, not link syntax — 39% of stale-ref findings on real repos
  were this noise class.

The fixes (in :mod:`roam.commands.cmd_stale_refs`):

* Bare-backtick scanning is now opt-in via ``--scan-bare-backticks``
  (default False).
* Even in opt-in mode, backtick matches inside a markdown link's
  ``[display]`` portion are suppressed — the URL is the source of
  truth for liveness.
* The ``--fix`` builder simulates each proposed rewrite and REFUSES
  any edit whose new URL fails to resolve OR produces a
  double-prefix path (belt-and-suspenders).

These tests guard ALL three layers of the fix. Failure of any of
them indicates a regression that would re-enable corruption of
working hyperlinks on real-world repos.
"""

from __future__ import annotations

import pytest

from roam.commands.cmd_stale_refs import (
    _build_fix_edits,
    _extract_refs,
    _has_repeated_segment_run,
    _rewrite_is_safe,
)
from tests.conftest import (
    git_init,
    invoke_cli,
    parse_json_output,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def bug1_project(tmp_path):
    """Repo modelled after the dogfood-reported bug:

    * ``docs/legacy/code-map/16-X.md`` exists on disk.
    * ``docs/legacy/reports/some-doc.md`` links to it as
      ``[`code-map/16-X.md`](../code-map/16-X.md)`` — the URL half is
      a valid relative path from ``reports/`` into ``code-map/``; the
      display half is purely cosmetic.

    The pre-fix scanner extracted BOTH halves and treated the display
    string as a path that didn't exist (``reports/code-map/16-X.md``),
    then proposed a "fix" that added ``docs/legacy/`` prefix to the
    URL — turning the working link into ``../docs/legacy/code-map/...``
    which, from ``reports/``, resolves to ``docs/legacy/docs/legacy/...``.
    """
    proj = tmp_path / "bug1_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "README.md").write_text("Repo root.\n")

    (proj / "docs" / "legacy" / "code-map").mkdir(parents=True)
    (proj / "docs" / "legacy" / "code-map" / "16-X.md").write_text("# Map 16\n\nContent.\n")

    (proj / "docs" / "legacy" / "reports").mkdir(parents=True)
    (proj / "docs" / "legacy" / "reports" / "some-doc.md").write_text(
        "# Report\n"
        "\n"
        "See [`code-map/16-X.md`](../code-map/16-X.md) for the map.\n"
    )
    git_init(proj)
    return proj


@pytest.fixture
def bug2_project(tmp_path):
    """Repo with bare backtick strings in prose — no claim about disk layout."""
    proj = tmp_path / "bug2_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "README.md").write_text(
        "# Notes\n"
        "\n"
        "Look at the `MyDataController.php` controller for an example.\n"
        "The `process()` method does the heavy lifting.\n"
        "Consider also `views/page.html` and `static/style.css`.\n"
    )
    git_init(proj)
    return proj


@pytest.fixture
def genuine_stale_project(tmp_path):
    """Repo with an honestly-broken hyperlink — positive control.

    The link target lives under the repo root but doesn't exist on
    disk. The pre-fix scanner correctly flagged this; the
    post-fix scanner must continue to.
    """
    proj = tmp_path / "genuine_stale"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "docs").mkdir()
    (proj / "docs" / "intro.md").write_text("See [gone](missing.md) for nothing.\n")
    (proj / "README.md").write_text("See [missing](docs/gone.md) for nothing.\n")
    git_init(proj)
    return proj


# ---------------------------------------------------------------------------
# Extractor-level tests (unit-level, no CLI invocation)
# ---------------------------------------------------------------------------


class TestExtractorBehavior:
    def test_url_not_display_resolved(self):
        """The display half of ``[`x`](url)`` must NOT be extracted as a path.

        Even when ``--scan-bare-backticks`` is opted into. The URL is
        the canonical source of truth for path liveness.
        """
        content = "See [`code-map/16-X.md`](../code-map/16-X.md) for details.\n"
        # Default mode: only the markdown link's URL surfaces.
        refs = _extract_refs(content, prose_mode=True)
        assert refs == [(1, "md_inline", "../code-map/16-X.md")]
        # Opt-in mode: backtick inside [display] is STILL suppressed.
        refs = _extract_refs(content, prose_mode=True, scan_bare_backticks=True)
        assert refs == [(1, "md_inline", "../code-map/16-X.md")]
        # Specifically: no `backtick` kind appears.
        kinds = {r[1] for r in refs}
        assert "backtick" not in kinds

    def test_bare_backtick_not_treated_as_path(self):
        """Bare backtick strings in prose must NOT be extracted by default."""
        content = "Look at the `Foo.php` controller for details.\n"
        refs = _extract_refs(content, prose_mode=True)
        # No backtick refs by default — backticks are inline code.
        assert refs == []

    def test_scan_bare_backticks_opt_in_flag(self):
        """The ``--scan-bare-backticks`` opt-in re-enables historical extraction."""
        content = "Look at the `Foo.php` controller for details.\n"
        refs = _extract_refs(content, prose_mode=True, scan_bare_backticks=True)
        assert refs == [(1, "backtick", "Foo.php")]


class TestSegmentRepeatDetector:
    """Unit tests for the double-prefix guard."""

    def test_docs_legacy_double_prefix_flagged(self):
        assert _has_repeated_segment_run("docs/legacy/docs/legacy/X.md") is True

    def test_single_segment_double_flagged(self):
        assert _has_repeated_segment_run("docs/docs/X.md") is True

    def test_legitimate_path_not_flagged(self):
        assert _has_repeated_segment_run("docs/legacy/code-map/16-X.md") is False
        assert _has_repeated_segment_run("a.md") is False
        assert _has_repeated_segment_run("a/b/c.md") is False

    def test_long_chain_repeat_flagged(self):
        # a/b/c repeated → flagged.
        assert _has_repeated_segment_run("a/b/c/a/b/c") is True


# ---------------------------------------------------------------------------
# Resolver-level tests
# ---------------------------------------------------------------------------


class TestUrlNotDisplayResolution:
    def test_canonical_bug1_link_not_flagged(self, cli_runner, bug1_project, monkeypatch):
        """The Bug 1 link `[`code-map/16-X.md`](../code-map/16-X.md)` from
        ``docs/legacy/reports/some-doc.md`` must NOT be flagged."""
        monkeypatch.chdir(bug1_project)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=bug1_project, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        targets = [t["target"] for t in data["targets"]]
        # No finding for the live target.
        assert all("code-map/16-X.md" not in t for t in targets), (
            f"Bug 1 regression: live link was flagged. Targets: {targets}"
        )
        assert data["summary"]["missing_targets"] == 0

    def test_valid_root_relative_link(self, cli_runner, tmp_path, monkeypatch):
        """A relative link `[home](../README.md)` from a nested doc must resolve."""
        proj = tmp_path / "root_rel"
        proj.mkdir()
        (proj / "README.md").write_text("# Root\n")
        (proj / "docs" / "a" / "b").mkdir(parents=True)
        (proj / "docs" / "a" / "b" / "note.md").write_text(
            "[home](../../../README.md)\n"
        )
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(cli_runner, ["stale-refs"], cwd=proj, json_mode=True)
        data = parse_json_output(result, "stale-refs")
        assert data["summary"]["missing_targets"] == 0


# ---------------------------------------------------------------------------
# --fix safety tests
# ---------------------------------------------------------------------------


class TestFixSafetyGuards:
    def test_fix_apply_dry_run_does_not_corrupt_bug1(
        self, cli_runner, bug1_project, monkeypatch
    ):
        """``stale-refs --fix preview`` on the Bug 1 fixture must propose
        ZERO rewrites — the link is already live and the only "fix" the
        broken pipeline ever proposed was corruption."""
        monkeypatch.chdir(bug1_project)
        result = invoke_cli(
            cli_runner,
            ["stale-refs", "--fix", "preview"],
            cwd=bug1_project,
            json_mode=True,
        )
        data = parse_json_output(result, "stale-refs")
        # Verify it's a fix-preview envelope.
        assert data["summary"].get("fix_mode") == "preview"
        # Zero rewrites planned — the link was always live.
        assert data["summary"]["edits_planned"] == 0
        assert data["summary"]["files_touched"] == 0

    def test_double_prefix_rewrite_refused(self, tmp_path):
        """Synthesise a target+hint pair where the proposed rewrite would
        produce ``docs/legacy/docs/legacy/X.md``; the safety guard must
        REFUSE it and log a clear reason."""
        # Build a tiny on-disk layout so the resolver has something
        # concrete to look at.
        proj = tmp_path / "refuse_proj"
        proj.mkdir()
        (proj / "docs" / "legacy" / "code-map").mkdir(parents=True)
        (proj / "docs" / "legacy" / "code-map" / "16-X.md").write_text("ok\n")
        (proj / "docs" / "legacy" / "reports").mkdir(parents=True)
        src_rel = "docs/legacy/reports/some-doc.md"
        (proj / src_rel).write_text("[`x`](../code-map/16-X.md)\n")

        # Hand-craft what the broken pipeline used to emit:
        # display half is "code-map/16-X.md", and the hint proposes
        # "docs/legacy/code-map/16-X.md" as the new URL. From
        # docs/legacy/reports/, that URL would resolve to
        # docs/legacy/reports/docs/legacy/code-map/16-X.md (missing) —
        # OR if rewritten as a relative URL "../docs/legacy/code-map/X.md"
        # it resolves to docs/legacy/docs/legacy/code-map/X.md (missing,
        # double-prefix).
        targets = [
            {
                "target": "docs/legacy/reports/code-map/16-X.md",
                "sources": [
                    {
                        "file": src_rel,
                        "line": 1,
                        "kind": "md_inline",
                        "raw": "../docs/legacy/code-map/16-X.md",
                    },
                ],
                "hint": {
                    "confidence": "HIGH",
                    "target": "../docs/legacy/code-map/16-X.md",
                    "reason": "synthetic for test",
                    "source": "synthetic",
                },
            }
        ]
        refused: list[str] = []
        edits = _build_fix_edits(targets, proj, refused_log=refused)
        # No edits should have made it through — the rewrite is unsafe.
        assert edits == {} or all(not v for v in edits.values()), (
            f"Unsafe rewrite slipped through: {edits}"
        )
        assert refused, "Expected at least one refusal line"
        # The log must clearly identify why we refused.
        joined = "\n".join(refused)
        assert "REFUSED" in joined

    def test_rewrite_is_safe_helper_refuses_no_op_rewrite(self, tmp_path):
        """If the ORIGINAL URL already resolves, the rewrite is a no-op
        (or worse: net-negative). Refuse it."""
        proj = tmp_path / "noop_proj"
        proj.mkdir()
        (proj / "docs" / "x.md").mkdir(parents=True)
        (proj / "docs" / "x.md" / "child.md").write_text("ok\n")
        (proj / "README.md").write_text("[x](docs/x.md/child.md)\n")
        safe, reason = _rewrite_is_safe(
            "README.md",
            "docs/x.md/child.md",  # already resolves live
            "different/path.md",  # any candidate replacement
            proj,
        )
        assert safe is False
        assert "already resolves" in reason

    def test_rewrite_is_safe_refuses_broken_replacement(self, tmp_path):
        """The new URL must resolve to an existing file."""
        proj = tmp_path / "broken_replace"
        proj.mkdir()
        (proj / "README.md").write_text("[x](old.md)\n")
        safe, reason = _rewrite_is_safe(
            "README.md",
            "old.md",  # doesn't exist
            "still-missing.md",  # also doesn't exist
            proj,
        )
        assert safe is False
        assert "does not resolve" in reason

    def test_rewrite_is_safe_accepts_genuine_rename(self, tmp_path):
        """A genuine rename — old is gone, new exists on disk — IS safe."""
        proj = tmp_path / "ok_rename"
        proj.mkdir()
        (proj / "new").mkdir()
        (proj / "new" / "notes.md").write_text("ok\n")
        safe, reason = _rewrite_is_safe(
            "README.md",
            "old/notes.md",  # gone
            "new/notes.md",  # exists
            proj,
        )
        assert safe is True
        assert reason == ""

    def test_proposed_rewrite_preserves_link_validity(
        self, cli_runner, tmp_path, monkeypatch
    ):
        """For every rewrite the fix pipeline proposes, the NEW URL must
        resolve to an existing file. This is the inverse of the corruption
        bug — we never replace a live OR dead link with a dead one."""
        # Stage a real rename scenario: README still points at old path,
        # but the file moved to new/notes.md.
        proj = tmp_path / "preserve_validity"
        proj.mkdir()
        (proj / ".gitignore").write_text(".roam/\n")
        (proj / "README.md").write_text("[notes](old/path/notes.md)\n")
        (proj / "new").mkdir()
        (proj / "new" / "notes.md").write_text("ok\n")
        git_init(proj)
        monkeypatch.chdir(proj)
        result = invoke_cli(
            cli_runner,
            ["stale-refs", "--fix", "preview"],
            cwd=proj,
            json_mode=True,
        )
        data = parse_json_output(result, "stale-refs")
        # If a rewrite was proposed, it must point at the live new path.
        if data["summary"].get("edits_planned", 0) > 0:
            diff = data.get("diff", "")
            # The proposed replacement must include new/notes.md.
            assert "new/notes.md" in diff, (
                f"Rewrite did not target the live path. Diff:\n{diff}"
            )


# ---------------------------------------------------------------------------
# Positive controls — make sure we didn't disable detection entirely.
# ---------------------------------------------------------------------------


class TestPositiveControls:
    def test_genuine_stale_ref_still_flagged(
        self, cli_runner, genuine_stale_project, monkeypatch
    ):
        """``[gone](../missing.md)`` where missing.md doesn't exist MUST
        still be flagged. The fix narrows false-positives, it doesn't
        disable the feature."""
        monkeypatch.chdir(genuine_stale_project)
        result = invoke_cli(
            cli_runner, ["stale-refs"], cwd=genuine_stale_project, json_mode=True
        )
        data = parse_json_output(result, "stale-refs")
        assert data["summary"]["missing_targets"] >= 1
        targets = [t["target"] for t in data["targets"]]
        assert any("missing.md" in t for t in targets), (
            f"Genuine stale ref was hidden. Targets: {targets}"
        )

    def test_bug2_bare_backtick_silent_by_default(
        self, cli_runner, bug2_project, monkeypatch
    ):
        """Bare-backtick prose noise produces ZERO findings by default."""
        monkeypatch.chdir(bug2_project)
        result = invoke_cli(
            cli_runner, ["stale-refs"], cwd=bug2_project, json_mode=True
        )
        data = parse_json_output(result, "stale-refs")
        assert data["summary"]["missing_targets"] == 0, (
            "Bare backticks in prose were treated as path claims. "
            f"Findings: {[t['target'] for t in data['targets']]}"
        )

    def test_bug2_bare_backtick_flagged_with_opt_in(
        self, cli_runner, bug2_project, monkeypatch
    ):
        """When ``--scan-bare-backticks`` is set, the historical behaviour
        re-engages: bare backtick strings ARE treated as references."""
        monkeypatch.chdir(bug2_project)
        result = invoke_cli(
            cli_runner,
            ["stale-refs", "--scan-bare-backticks"],
            cwd=bug2_project,
            json_mode=True,
        )
        data = parse_json_output(result, "stale-refs")
        # At least one bare-backtick mention (e.g. views/page.html) is
        # now picked up as a missing-path claim.
        assert data["summary"]["missing_targets"] >= 1, (
            "Opt-in flag did not re-enable bare-backtick detection."
        )
