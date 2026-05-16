"""Bootstrap-command always-allowed regression tests (W23.3 follow-through).

The mode-enforcement gate in ``src/roam/cli.py`` keeps a hard-coded set
of commands (``_MODE_ALWAYS_ALLOWED``) that bypass mode checks. The
historical set covered meta / discovery commands but DID NOT include
the index-bootstrap commands (``init``, ``index``). That created a
latent chicken-and-egg deadlock:

  1. Fresh repo with no ``.roam/active_mode`` file.
  2. Default mode resolves to ``safe_edit``.
  3. User exports ``ROAM_MODE_ENFORCEMENT=1`` (e.g. in CI).
  4. ``roam init`` is blocked because ``init`` is not in any mode's
     allow-list (verified against ``roam.modes.policy._MODE_EXTRAS``).
  5. The user has no way to make progress — even switching mode is
     useless because they still cannot index anything afterwards.

These tests pin the fix so a future refactor of the allow-list cannot
silently regress the bootstrap path.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent))


# ---------------------------------------------------------------------------
# Static invariant: the always-allowed set must list bootstrap commands.
# ---------------------------------------------------------------------------


def test_bootstrap_commands_in_always_allowed():
    """``index`` and ``init`` must bypass mode enforcement.

    Rationale: a user in a fresh repo (no .roam/active_mode) cannot
    bootstrap into ANY usable state without running these commands
    first. Removing them from ``_MODE_ALWAYS_ALLOWED`` creates a
    chicken-and-egg deadlock the moment ``ROAM_MODE_ENFORCEMENT=1`` is
    exported.

    Note: ``reindex`` is not currently a registered command in
    ``cli._COMMANDS``; if it ever gains a registration entry, this
    invariant should be extended to cover it too.
    """
    from roam.cli import _MODE_ALWAYS_ALLOWED

    assert "index" in _MODE_ALWAYS_ALLOWED
    assert "init" in _MODE_ALWAYS_ALLOWED


def test_bootstrap_commands_not_redundantly_in_mode_extras():
    """Sanity: the bootstrap commands are NOT covered by per-mode allow-lists.

    If a future change moves ``init``/``index`` into every mode's
    extras set, the always-allowed entry becomes redundant. That's
    fine, but it should be a conscious choice. This test pins the
    current state so the rationale above stays accurate.
    """
    from roam.modes.policy import _MODE_EXTRAS

    for cmd in ("init", "index"):
        per_mode = {m: cmd in s for m, s in _MODE_EXTRAS.items()}
        assert not any(per_mode.values()), (
            f"{cmd!r} is in a mode extras set ({per_mode}); the "
            f"always-allowed entry may now be redundant — update the "
            f"comment in cli._MODE_ALWAYS_ALLOWED accordingly."
        )


# ---------------------------------------------------------------------------
# Behavioural smoke: enforcement-on does not block the bootstrap path.
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_repo(tmp_path, monkeypatch):
    """Bare repo with a ``.git`` marker. No constitution, no active_mode."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    monkeypatch.delenv("ROAM_AGENT_MODE", raising=False)
    monkeypatch.delenv("ROAM_MODE_ENFORCEMENT", raising=False)
    monkeypatch.delenv("ROAM_RUN_ID", raising=False)
    monkeypatch.chdir(repo)
    return repo


@pytest.fixture
def runner():
    return CliRunner()


def _invoke(runner, args, repo, env=None):
    from roam.cli import cli

    full_env = os.environ.copy()
    for k in ("ROAM_AGENT_MODE", "ROAM_MODE_ENFORCEMENT", "ROAM_RUN_ID"):
        full_env.pop(k, None)
    if env:
        full_env.update(env)
    old = os.getcwd()
    try:
        os.chdir(str(repo))
        result = runner.invoke(cli, args, catch_exceptions=False, env=full_env)
    finally:
        os.chdir(old)
    return result


@pytest.mark.parametrize("cmd", [["init", "--help"], ["index", "--help"]])
def test_bootstrap_unblocked_with_enforcement_on(runner, fresh_repo, cmd):
    """``roam init --help`` / ``roam index --help`` must run with enforcement.

    The fresh repo has no ``.roam/active_mode`` file, so mode resolves
    to ``safe_edit`` — which does NOT list ``init``/``index`` in its
    extras. The gate must therefore consult ``_MODE_ALWAYS_ALLOWED``
    and let dispatch proceed. We use ``--help`` to keep the test fast
    and avoid side-effects on the temp repo.
    """
    result = _invoke(
        runner,
        cmd,
        fresh_repo,
        env={"ROAM_MODE_ENFORCEMENT": "1"},
    )
    # Click --help exits 0.
    assert result.exit_code == 0, (
        f"{cmd!r} blocked under ROAM_MODE_ENFORCEMENT=1: "
        f"exit={result.exit_code} stdout={result.output!r} "
        f"stderr={getattr(result, 'stderr', '')!r}"
    )
    combined = (result.output or "") + (getattr(result, "stderr", "") or "")
    # The gate's signature message must NOT appear.
    assert "Pass `--override-mode` to bypass" not in combined, f"{cmd!r} hit the mode gate: {combined}"
