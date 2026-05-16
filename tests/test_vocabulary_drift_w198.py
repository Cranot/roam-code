"""W198 — vocabulary drift fixes from the W186 8-questions audit.

Three drifts were identified and addressed in this wave:

1. ``pr-risk`` + ``bus-factor`` use ``author`` (git-blame vocabulary)
   while ``audit-trail`` correctly uses ``actor`` (the W182 agentic-
   assurance crosswalk vocabulary). Per CLAUDE.md Pattern 3 ("Vocabulary
   mismatch across commands"), the fix is **not** a global rename —
   ``author`` stays as the git-blame term for back-compat, but every
   envelope that feeds ``ChangeEvidence`` gets a parallel ``actor``
   field with the same value so the downstream packet doesn't carry
   two synonyms.

2. ``roam permit`` is documented as a verdict facade (ALLOW / REVIEW /
   BLOCK over a diff or symbol). It does NOT persist a permit_id to
   ``.roam/permits/``. W182's ``AuthorityRef(authority_kind="permit")``
   anticipates an explicit permit-override identity — today's command
   only satisfies the verdict surface. The gap is surfaced as
   documentation in two places: the ``cmd_permit.py`` module docstring
   and the ``AUTHORITY_KINDS["permit"]`` entry in
   ``src/roam/evidence/_vocabulary.py``.

3. ``RunMeta.agent`` (unsuffixed) vs ``ChangeEvidence.agent_id``
   (id-suffixed). Same meaning, different name for historical reasons.
   The fix is an explanatory comment at ``RunMeta.agent`` pointing at
   the mapping site in ``evidence/collector.py:_build_actor_refs``.

These tests pin the documentation and parallel-field promises so a
future refactor that drops them will fail loudly.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from click.testing import CliRunner

from roam.cli import cli

# ---------------------------------------------------------------------------
# Drift 1a: pr-risk envelope carries both author and actor
# ---------------------------------------------------------------------------


def _stage_pr_risk_change(project: Path) -> None:
    """Modify a tracked file so pr-risk has a non-empty diff to analyse.

    Mirrors the helper in ``tests/test_findings_pr_risk.py`` so we don't
    couple the two tests on a shared private fixture.
    """
    target = project / "src" / "models.py"
    if not target.exists():
        # Fallback: write a small change wherever pyproject lives. The
        # indexed_project fixture (see conftest) provides ``src/models.py``,
        # but be defensive in case the fixture changes.
        candidates = list((project / "src").glob("*.py")) if (project / "src").exists() else []
        if candidates:
            target = candidates[0]
        else:
            target = project / "models.py"
            target.write_text("def f():\n    return 1\n", encoding="utf-8")
            return
    existing = target.read_text(encoding="utf-8")
    target.write_text(
        existing + "\n# W198 vocabulary-drift smoke change\n",
        encoding="utf-8",
    )


def test_pr_risk_envelope_carries_both_author_and_actor(indexed_project):
    """pr-risk --json envelope has both ``author`` and ``actor`` with the same value.

    The git-vocabulary surface (``author``) is kept for back-compat;
    the crosswalk surface (``actor``) is added so downstream evidence
    collectors don't carry two synonyms. Both must be present and
    must hold the same value.
    """
    _stage_pr_risk_change(indexed_project)

    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(indexed_project))
        # ``roam --json pr-risk`` emits the JSON envelope on stdout.
        result = runner.invoke(cli, ["--json", "pr-risk"], catch_exceptions=False)
        assert result.exit_code == 0, result.output

        payload = json.loads(result.output)

        # Both keys present at the top level of the envelope.
        assert "author" in payload, (
            "pr-risk envelope is missing the git-blame ``author`` field (W198 back-compat surface)"
        )
        assert "actor" in payload, (
            "pr-risk envelope is missing the ``actor`` field (W198 / W182 ActorRef crosswalk vocabulary)"
        )
        # Same identity — they're two names for one value.
        assert payload["author"] == payload["actor"], (
            "pr-risk ``author`` and ``actor`` should hold the same value; got author={!r} actor={!r}".format(
                payload["author"], payload["actor"]
            )
        )
    finally:
        os.chdir(old_cwd)


def test_pr_risk_suggested_reviewers_carry_both_author_and_actor(indexed_project):
    """Each entry in ``suggested_reviewers[]`` has both ``author`` and ``actor``."""
    _stage_pr_risk_change(indexed_project)

    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(indexed_project))
        result = runner.invoke(cli, ["--json", "pr-risk"], catch_exceptions=False)
        assert result.exit_code == 0, result.output

        payload = json.loads(result.output)
        reviewers = payload.get("suggested_reviewers") or []
        # The fixture may produce zero reviewers on a single-commit repo;
        # that's a valid envelope state. Only enforce the contract on
        # rows that exist.
        for row in reviewers:
            assert "author" in row, f"suggested_reviewers row missing ``author`` (git-blame): {row!r}"
            assert "actor" in row, f"suggested_reviewers row missing ``actor`` (crosswalk): {row!r}"
            assert row["author"] == row["actor"], f"suggested_reviewers row drift: {row!r}"
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Drift 1b: bus-factor envelope carries both author and actor
# ---------------------------------------------------------------------------


def test_bus_factor_envelope_carries_both_author_and_actor(indexed_project):
    """bus-factor --json envelope: every directory row has both
    ``primary_author`` and ``primary_actor`` (W198 parallel fields), and
    every ``top_authors`` entry has both ``name`` and ``actor``.
    """
    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(indexed_project))
        # ``--force-team-mode`` keeps the full ranking surface even on a
        # single-author fixture so the directories[] array isn't empty.
        result = runner.invoke(cli, ["--json", "bus-factor", "--force-team-mode"], catch_exceptions=False)
        assert result.exit_code == 0, result.output

        payload = json.loads(result.output)
        directories = payload.get("directories") or []

        # ``indexed_project`` may produce zero directories when no git
        # history yet exists on the fixture repo — in that case the
        # envelope is valid but doesn't exercise the contract. Use a
        # soft skip in that case so the test pins behaviour where there
        # IS data and stays green otherwise.
        if not directories:
            return

        for row in directories:
            assert "primary_author" in row, (
                f"bus-factor directory row missing ``primary_author`` (git-blame back-compat): {row!r}"
            )
            assert "primary_actor" in row, (
                f"bus-factor directory row missing ``primary_actor`` (W198 / W182 crosswalk vocabulary): {row!r}"
            )
            assert row["primary_author"] == row["primary_actor"], (
                f"bus-factor primary_author/primary_actor drift: {row!r}"
            )

            # ``top_authors`` rows keep ``name`` (existing) and add
            # ``actor`` (W198 crosswalk alias of ``name``).
            for a in row.get("top_authors") or []:
                assert "name" in a, f"top_authors row missing ``name``: {a!r}"
                assert "actor" in a, f"top_authors row missing W198 ``actor`` alias: {a!r}"
                assert a["name"] == a["actor"], f"top_authors name/actor drift: {a!r}"
    finally:
        os.chdir(old_cwd)


# ---------------------------------------------------------------------------
# Drift 2: roam permit docstring documents the verdict-facade gap
# ---------------------------------------------------------------------------


def test_permit_command_docstring_warns_about_facade_state():
    """``cmd_permit.py`` module docstring AND the click-command function
    docstring both explicitly call out the verdict-facade state and the
    "no permit_id is persisted" gap (W198 audit finding).

    Pinning this guards against silent removal in a future cleanup that
    drops the documentation but leaves the semantic gap in place.
    """
    from roam.commands import cmd_permit

    module_doc = cmd_permit.__doc__ or ""
    # Module-level docstring must surface both the facade framing and
    # the explicit "no permit_id" gap.
    assert "verdict facade" in module_doc.lower(), (
        "cmd_permit module docstring should call out the verdict-facade framing per W198 audit finding"
    )
    assert "permit_id" in module_doc.lower() or "permit identity" in module_doc.lower(), (
        "cmd_permit module docstring should explicitly note that no permit_id is persisted (W198 audit finding)"
    )

    # The click command's own docstring should ALSO call out the gap so
    # ``roam permit --help`` users see it. The click command object
    # exposes the function docstring via ``.help``.
    permit_cmd = cmd_permit.permit_cmd
    help_text = (permit_cmd.help or "") + " " + (permit_cmd.__doc__ or "")
    assert "verdict facade" in help_text.lower(), (
        "cmd_permit's click-command docstring should call out the "
        "verdict-facade framing (visible via ``roam permit --help``)"
    )
    assert "permit_id" in help_text.lower(), (
        "cmd_permit's click-command docstring should explicitly note that no permit_id is persisted"
    )


def test_authority_kinds_permit_docstring_warns_about_facade():
    """``_vocabulary.py`` source file: the ``AUTHORITY_KINDS["permit"]``
    sphinx comment must explicitly note the W198 facade gap.

    The vocabulary file uses ``#:`` sphinx comments above each frozenset
    entry, so the documentation lives in the source file (not in a
    ``__doc__`` attribute). We grep the file content for the W198
    wording near the ``permit`` token to assert the explainer is present.
    """
    from roam.evidence import _vocabulary

    source = Path(_vocabulary.__file__).read_text(encoding="utf-8")
    # Find the comment block describing the ``permit`` authority kind.
    # The comment must mention BOTH the "verdict-only facade" framing
    # and the "no permit_id" gap so the next sprint sees the explainer
    # at the point of definition.
    perm_idx = source.find("``permit``")
    assert perm_idx != -1, (
        "AUTHORITY_KINDS docstring should reference ``permit`` so the W198 explainer has a stable anchor"
    )
    # Pull a window of text around the permit entry — large enough to
    # span the multi-line comment block.
    window = source[perm_idx : perm_idx + 1500]
    assert "W198" in window, (
        "AUTHORITY_KINDS[permit] comment should reference W198 (the audit finding that surfaced the facade gap)"
    )
    assert "verdict-only facade" in window or "verdict facade" in window, (
        "AUTHORITY_KINDS[permit] comment should call out the verdict-only facade state"
    )
    assert "permit_id" in window, (
        "AUTHORITY_KINDS[permit] comment should explicitly note that no permit_id is persisted today"
    )


# ---------------------------------------------------------------------------
# Drift 3: RunMeta.agent has the W198 explainer comment
# ---------------------------------------------------------------------------


def test_run_meta_agent_field_has_w198_explainer_comment():
    """The ``agent`` field on ``RunMeta`` carries an inline comment
    documenting the ``agent`` -> ``agent_id``/``actor_id`` mapping.

    W190's ``_build_actor_refs`` maps ``RunMeta.agent`` to
    ``ActorRef(actor_kind="agent", actor_id=...)``; the inline
    comment at the dataclass field is the canonical place to see the
    mapping intent (per CLAUDE.md Pattern 3, vocabulary mismatch must
    be documented at the data definition, not only at the call site).
    """
    from roam.runs import ledger

    source = Path(ledger.__file__).read_text(encoding="utf-8")
    # The explainer comment must mention W198 (the wave) and reference
    # both the agent_id alias and the collector mapping site so a
    # future reader has a stable anchor.
    assert "W198" in source, "runs/ledger.py should reference W198 in the vocabulary-note comment near RunMeta.agent"
    assert "agent_id" in source, (
        "runs/ledger.py W198 comment should mention the crosswalk "
        "``agent_id`` name so the mapping is visible at the data "
        "definition"
    )
    assert "_build_actor_refs" in source, (
        "runs/ledger.py W198 comment should point at the evidence/collector.py:_build_actor_refs mapping site"
    )
