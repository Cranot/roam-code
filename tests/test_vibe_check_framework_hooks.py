"""W161: vibe-check's dead-export detector exempts framework-callback names.

Background
----------

The W149 dogfood audit found that the ``dead_exports`` category emitted
405 findings on roam-code, of which two clusters were systemic false
positives:

* Click ``MultiCommand`` overrides on ``cli.py:LazyGroup`` — methods like
  ``list_commands`` / ``get_command`` / ``parse_args`` / ``invoke`` are
  invoked by Click's runtime via duck-typing; the static call graph has
  zero edges to them.
* ``as_envelope_dict`` methods in ``quality/cycles.py``,
  ``quality/god_components.py``, and ``quality/ai_rot.py`` — consumers call
  ``obj.as_envelope_dict()`` via attribute reflection on a result object.

W161 ships a name-based allowlist (``_FRAMEWORK_HOOK_NAMES``) consulted
at the SQL level in ``_detect_dead_exports`` and
``_collect_dead_export_findings``. This test suite covers:

1. The allowlist contains the dogfood-derived noise classes.
2. A synthetic fixture flags genuine dead methods but not framework hooks.
3. The persisted ``findings`` rows don't contain any allowlisted name.

The class-hierarchy variant (exempt anything inheriting from
``click.MultiCommand`` / ``click.Group`` / ``click.Command``) is
deferred — the name-based approach catches the high-FP cases the audit
surfaced and avoids walking the symbols hierarchy at detection time.
"""

from __future__ import annotations

import json
import os
import sqlite3

from click.testing import CliRunner

from roam.cli import cli
from roam.commands.cmd_vibe_check import _FRAMEWORK_HOOK_NAMES
from roam.db.connection import open_db
from tests.conftest import make_src_project as _make_project

# ---------------------------------------------------------------------------
# Allowlist composition (cheap, runs without indexing)
# ---------------------------------------------------------------------------


def test_framework_hook_allowlist_covers_click_multicommand_methods():
    """Click ``MultiCommand`` / ``Group`` method names are allowlisted.

    These are the names called by Click's runtime on every
    ``roam <subcmd>`` invocation. The static call graph cannot resolve
    them; without the allowlist they're flagged as dead exports.
    """
    must_have = {
        "list_commands",
        "get_command",
        "resolve_command",
        "parse_args",
        "format_help",
        "invoke",
    }
    missing = must_have - _FRAMEWORK_HOOK_NAMES
    assert not missing, f"Click MultiCommand hooks missing from allowlist: {missing}"


def test_framework_hook_allowlist_covers_reflective_serialisers():
    """``as_envelope_dict`` and sibling reflection-called methods are allowlisted.

    ``roam/quality/cycles.py``, ``god_components.py``, and ``ai_rot.py``
    expose ``as_envelope_dict`` called via attribute reflection — the indexer
    can't resolve the attribute access at extract time.
    """
    must_have = {
        "as_envelope_dict",
        "as_dict",
        "to_dict",
        "to_json",
        "from_dict",
        "from_json",
    }
    missing = must_have - _FRAMEWORK_HOOK_NAMES
    assert not missing, f"Reflective serialisation hooks missing from allowlist: {missing}"


def test_framework_hook_allowlist_is_a_frozenset():
    """Allowlist is immutable so detector-tune drift requires a source edit."""
    assert isinstance(_FRAMEWORK_HOOK_NAMES, frozenset)
    # Sanity check on cardinality — 50 entries at W161 ship.
    # Tighten if a future tune expands it; loosen never required.
    assert len(_FRAMEWORK_HOOK_NAMES) >= 40, (
        f"allowlist shrank to {len(_FRAMEWORK_HOOK_NAMES)} entries — "
        "regression risk: removing names re-introduces W149 false positives"
    )


# ---------------------------------------------------------------------------
# Synthetic fixture: hook names exempted, real dead method flagged
# ---------------------------------------------------------------------------


def _hook_vs_dead_project(tmp_path):
    """Project with one class whose methods exercise the allowlist.

    ``ResultEnvelope.as_envelope_dict`` and ``ResultEnvelope.__init__`` are
    in the allowlist; ``ResultEnvelope.helper_unused`` is not — that's the
    genuine dead export the detector should still flag.

    All three methods are exported (no leading underscore) and have zero
    incoming edges (nothing in the fixture calls them). Only
    ``helper_unused`` should reach the dead-export finding set.
    """
    return _make_project(
        tmp_path,
        {
            "envelope.py": '''
            class ResultEnvelope:
                """Synthetic envelope class with framework + reflective hooks."""

                def __init__(self, payload):
                    self.payload = payload

                def as_envelope_dict(self):
                    return {"payload": self.payload}

                def helper_unused(self):
                    """Genuine dead method — should appear in dead_exports."""
                    return 42

            def caller_main():
                """Anchors the module so it isn't pruned as test-only."""
                return ResultEnvelope({"k": "v"})
            ''',
            "main.py": """
            from .envelope import caller_main

            def entry():
                return caller_main()
            """,
        },
    )


def test_framework_hooks_excluded_from_dead_exports(tmp_path):
    """Allowlisted names don't appear in the persisted dead-export findings."""
    proj = _hook_vs_dead_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        result = runner.invoke(cli, ["vibe-check", "--persist"])
        assert result.exit_code == 0, result.output

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT evidence_json FROM findings "
                "WHERE source_detector = 'vibe-check' "
                "AND finding_id_str LIKE 'vibe-check:dead_exports:%'"
            ).fetchall()

        flagged_names = {json.loads(r["evidence_json"])["name"] for r in rows}

        # Framework hooks must not be flagged.
        for hook in ("as_envelope_dict", "__init__"):
            assert hook not in flagged_names, (
                f"{hook!r} flagged as dead-export despite being on the "
                f"framework-hook allowlist; full set: {sorted(flagged_names)}"
            )
    finally:
        os.chdir(old_cwd)


def test_genuinely_dead_method_still_flagged(tmp_path):
    """Real dead methods (not in the allowlist) keep being flagged.

    This is the bookend regression: the allowlist must NOT mask actual
    dead-code findings. ``helper_unused`` has zero incoming edges and is
    not on the allowlist — it must appear in the dead-export finding set.
    """
    proj = _hook_vs_dead_project(tmp_path)
    old_cwd = os.getcwd()
    try:
        os.chdir(str(proj))
        runner = CliRunner()
        assert runner.invoke(cli, ["index"]).exit_code == 0
        result = runner.invoke(cli, ["vibe-check", "--persist"])
        assert result.exit_code == 0, result.output

        with open_db(readonly=True) as conn:
            rows = conn.execute(
                "SELECT evidence_json FROM findings "
                "WHERE source_detector = 'vibe-check' "
                "AND finding_id_str LIKE 'vibe-check:dead_exports:%'"
            ).fetchall()

        flagged_names = {json.loads(r["evidence_json"])["name"] for r in rows}
        assert "helper_unused" in flagged_names, (
            "expected the genuinely-dead helper_unused to be flagged; "
            f"allowlist over-broad. Full set: {sorted(flagged_names)}"
        )
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Cross-detector regression on roam-code itself
# ---------------------------------------------------------------------------


def test_lazy_group_methods_not_in_dead_exports_on_roam_code():
    """Run vibe-check on roam-code and confirm Click hooks are suppressed.

    This is the W149 dogfood regression — without the allowlist,
    ``cli.py:LazyGroup``'s ``list_commands`` / ``get_command`` /
    ``resolve_command`` / ``parse_args`` / ``invoke`` were the headline
    false positives. None of them should appear in the persisted finding
    set after W161.

    Skipped when the working tree's findings table is missing — this is
    not a sandboxed fixture, it reads the real roam-code project DB.
    """
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # The findings table is W89+; gate on its presence so older clones
    # don't break the test.
    try:
        with open_db(readonly=True) as conn:
            try:
                conn.execute("SELECT 1 FROM findings LIMIT 1").fetchone()
            except sqlite3.OperationalError:
                import pytest

                pytest.skip("findings table not present in working DB")
            rows = conn.execute(
                "SELECT evidence_json FROM findings "
                "WHERE source_detector = 'vibe-check' "
                "AND finding_id_str LIKE 'vibe-check:dead_exports:%'"
            ).fetchall()
    except sqlite3.OperationalError:
        import pytest

        pytest.skip("no roam DB at working-tree default location")

    if not rows:
        # No persisted dead_exports yet — the suppression is correct by
        # vacuous truth but offers no regression signal. Skip rather than
        # falsely pass.
        import pytest

        pytest.skip("no persisted vibe-check dead_exports findings; run `roam vibe-check --persist` once to populate")

    flagged = []
    for r in rows:
        ev = json.loads(r["evidence_json"])
        if ev.get("file_path", "").endswith("src/roam/cli.py") or ev.get("file_path", "").endswith("src\\roam\\cli.py"):
            flagged.append(ev.get("name"))

    forbidden = {
        "list_commands",
        "get_command",
        "resolve_command",
        "parse_args",
        "invoke",
        "format_help",
    }
    leaked = forbidden.intersection(flagged)
    assert not leaked, (
        f"Click framework hooks still flagged on roam-code/cli.py: {leaked}. "
        "_FRAMEWORK_HOOK_NAMES must list them; SQL filter may be skipped."
    )

    # bonus: project_root is used to compute the file_path match — kept
    # so the variable isn't dropped to a linter warning.
    assert project_root


def test_as_envelope_dict_not_in_dead_exports_on_roam_code():
    """``as_envelope_dict`` is the reflective-callback counterpart of W161.

    Same gating as the Click test — skip if the registry hasn't been
    populated; otherwise assert no ``as_envelope_dict`` row survives.
    """
    try:
        with open_db(readonly=True) as conn:
            try:
                conn.execute("SELECT 1 FROM findings LIMIT 1").fetchone()
            except sqlite3.OperationalError:
                import pytest

                pytest.skip("findings table not present in working DB")
            rows = conn.execute(
                "SELECT evidence_json FROM findings "
                "WHERE source_detector = 'vibe-check' "
                "AND finding_id_str LIKE 'vibe-check:dead_exports:%'"
            ).fetchall()
    except sqlite3.OperationalError:
        import pytest

        pytest.skip("no roam DB at working-tree default location")

    if not rows:
        import pytest

        pytest.skip("no persisted vibe-check dead_exports findings; run `roam vibe-check --persist` once to populate")

    flagged = [json.loads(r["evidence_json"]).get("name") for r in rows]
    assert "as_envelope_dict" not in flagged, "as_envelope_dict still flagged on roam-code despite W161 allowlist"
