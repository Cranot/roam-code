"""`roam health` must not report Roam's own output as index drift.

Cold-visitor regression: on a pristine clone the documented golden path is
``roam init`` then ``roam health``. ``init`` writes ``.roam/`` and
``.roamignore``; ``git status --porcelain`` then shows both as ``??``; and the
working-tree drift check counted them, so command 3 of 4 opened with

    NOTE: 2 file(s) modified in working tree since last index —
          run `roam index` to refresh symbol/edge data.

naming the two files command 2 had just created. Re-running ``roam index``
could never clear it. These tests pin the classifier that fixed it, in both
directions: Roam's own artifacts are excused, real source edits are not.
"""

from __future__ import annotations

from roam.commands.resolve import _is_roam_own_artifact, _porcelain_path


class TestPorcelainPath:
    def test_plain_entry(self):
        assert _porcelain_path("?? .roamignore") == ".roamignore"
        assert _porcelain_path(" M src/app.py") == "src/app.py"

    def test_rename_uses_destination(self):
        assert _porcelain_path("R  old/name.py -> new/name.py") == "new/name.py"

    def test_quoted_path_is_unwrapped(self):
        assert _porcelain_path('?? "src/od d name.py"') == "src/od d name.py"

    def test_dot_slash_prefix_stripped_without_eating_leading_dot(self):
        """``lstrip("./")`` would turn ``.roamignore`` into ``roamignore``."""
        assert _porcelain_path("?? ./src/app.py") == "src/app.py"
        assert _porcelain_path("?? .roamignore") == ".roamignore"


class TestRoamArtifactsExcusedFromDrift:
    def test_roam_dir_never_counts(self):
        for entry in (
            "?? .roam/",
            "?? .roam/index.db",
            " M .roam/fitness.yaml",
            "?? .roam/cache/algconn.json",
        ):
            assert _is_roam_own_artifact(entry), entry

    def test_untracked_init_config_does_not_count(self):
        """Exactly what `roam init` leaves behind on a fresh clone."""
        assert _is_roam_own_artifact("?? .roamignore")
        assert _is_roam_own_artifact("?? .roamignore-findings")


class TestRealDriftStillCounts:
    def test_user_source_counts(self):
        for entry in (" M src/app.py", "?? new_module.py", "A  tests/test_x.py"):
            assert not _is_roam_own_artifact(entry), entry

    def test_tracked_roamignore_edit_counts(self):
        """Editing an adopted `.roamignore` really does change index scope.

        The excuse is scoped to the untracked (``??``) state — the file as
        `roam init` just wrote it. Once the user commits it, a modification is
        genuine drift and must still be reported.
        """
        assert not _is_roam_own_artifact(" M .roamignore")
        assert not _is_roam_own_artifact("M  .roamignore")

    def test_lookalike_paths_outside_roam_dir_count(self):
        """`.roam/` is a prefix match — don't let it swallow neighbours."""
        assert not _is_roam_own_artifact(" M .roamrc")
        assert not _is_roam_own_artifact(" M src/.roamignore")
        assert not _is_roam_own_artifact(" M roam/index.py")
