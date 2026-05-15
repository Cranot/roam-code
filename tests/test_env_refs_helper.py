"""W266 - tests for the shared environment-ref builder.

Pins the contract of :func:`roam.evidence.env_refs.build_environment_refs`:

* Returns a tuple of :class:`EnvironmentRef` rows in canonical order
  (``ci_job`` -> ``workspace`` -> ``branch_range`` -> ``local_run``).
* CI provider detection delegates to the same
  ``_CI_PROVIDER_ENV_VARS`` table the W251 matrix pins.
* Workspace ref is ALWAYS present (either from ``workspace_root`` or
  ``os.getcwd()``).
* ``branch_range`` only appears when a non-empty ``commit_range`` is
  provided.
* ``local_run`` only appears when no CI was detected (so a CI-run
  packet doesn't double-stamp hostname + ci_job).

The W251 test suite covers ``_detect_ci_env_id`` directly; this file
covers the assembly layer one level up.
"""

from __future__ import annotations

import os

import pytest

# All CI env vars the helper probes - scrubbed before every test so the
# suite is deterministic regardless of where it runs (developer laptop
# vs GitHub Actions vs CI).
_CI_ENV_VARS_TO_SCRUB: tuple[str, ...] = (
    "CI",
    "GITHUB_ACTIONS", "GITHUB_RUN_ID",
    "GITLAB_CI", "CI_JOB_ID",
    "BUILDKITE", "BUILDKITE_BUILD_ID",
    "CIRCLECI", "CIRCLE_BUILD_NUM",
    "JENKINS_URL", "BUILD_TAG",
    "TF_BUILD", "BUILD_BUILDID",
)


def _scrub_ci_env(monkeypatch) -> None:
    for var in _CI_ENV_VARS_TO_SCRUB:
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# Core contract: CI vs no-CI
# ---------------------------------------------------------------------------


def test_build_environment_refs_returns_ci_job_when_provider_detected(
    monkeypatch,
) -> None:
    """When CI env vars are set, a ``ci_job`` ref is produced first."""
    _scrub_ci_env(monkeypatch)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_RUN_ID", "ci-42")

    from roam.evidence.env_refs import build_environment_refs

    refs = build_environment_refs(workspace_root="/tmp/x")

    kinds = [r.env_kind for r in refs]
    assert "ci_job" in kinds, f"expected ci_job ref, got {kinds}"
    # CI suppresses the local_run fallback.
    assert "local_run" not in kinds, (
        f"local_run should not appear in a CI run; got {kinds}"
    )
    # The ci_job ref carries the value-var content.
    ci_ref = next(r for r in refs if r.env_kind == "ci_job")
    assert ci_ref.env_id == "ci-42", (
        f"unexpected ci_job env_id: {ci_ref.env_id!r}"
    )


def test_build_environment_refs_returns_local_run_when_no_ci(
    monkeypatch,
) -> None:
    """No CI -> a ``local_run`` ref with the machine's hostname."""
    _scrub_ci_env(monkeypatch)

    from roam.evidence.env_refs import build_environment_refs

    refs = build_environment_refs(workspace_root="/tmp/x")

    kinds = [r.env_kind for r in refs]
    assert "local_run" in kinds, f"expected local_run ref, got {kinds}"
    assert "ci_job" not in kinds, (
        f"ci_job should not appear with no CI env; got {kinds}"
    )
    local_ref = next(r for r in refs if r.env_kind == "local_run")
    # env_id is non-empty (hostname or the literal "local" fallback).
    assert isinstance(local_ref.env_id, str) and local_ref.env_id, (
        f"local_run env_id must be non-empty; got {local_ref.env_id!r}"
    )


# ---------------------------------------------------------------------------
# Workspace ref is always present
# ---------------------------------------------------------------------------


def test_build_environment_refs_includes_workspace_ref(monkeypatch) -> None:
    """A workspace ref MUST appear on every result."""
    _scrub_ci_env(monkeypatch)

    from roam.evidence.env_refs import build_environment_refs

    refs = build_environment_refs(workspace_root="/repo/example")

    workspace = [r for r in refs if r.env_kind == "workspace"]
    assert len(workspace) == 1, (
        f"expected exactly one workspace ref, got {workspace}"
    )
    assert workspace[0].env_id == "/repo/example", (
        f"workspace env_id should echo the input; got {workspace[0].env_id!r}"
    )


def test_build_environment_refs_falls_back_to_cwd_when_no_workspace_root(
    monkeypatch, tmp_path,
) -> None:
    """No workspace_root arg -> os.getcwd() is used."""
    _scrub_ci_env(monkeypatch)
    monkeypatch.chdir(tmp_path)

    from roam.evidence.env_refs import build_environment_refs

    refs = build_environment_refs()

    workspace = [r for r in refs if r.env_kind == "workspace"]
    assert len(workspace) == 1
    # tmp_path may be a symlinked / canonicalised path on macOS, so we
    # compare via resolve() to avoid spurious mismatches.
    expected = os.path.realpath(str(tmp_path))
    actual = os.path.realpath(workspace[0].env_id)
    assert expected == actual, (
        f"workspace ref should mirror cwd; expected {expected}, got {actual}"
    )


# ---------------------------------------------------------------------------
# branch_range ref appears only when commit_range is provided
# ---------------------------------------------------------------------------


def test_build_environment_refs_includes_branch_range_when_provided(
    monkeypatch,
) -> None:
    """commit_range="abc..def" -> a branch_range ref carries it."""
    _scrub_ci_env(monkeypatch)

    from roam.evidence.env_refs import build_environment_refs

    refs = build_environment_refs(
        commit_range="abc1234..def5678",
        workspace_root="/repo/example",
    )

    branch = [r for r in refs if r.env_kind == "branch_range"]
    assert len(branch) == 1, (
        f"expected exactly one branch_range ref, got {branch}"
    )
    assert branch[0].env_id == "abc1234..def5678", (
        f"branch_range env_id should echo the input; "
        f"got {branch[0].env_id!r}"
    )


def test_build_environment_refs_omits_branch_range_when_absent(
    monkeypatch,
) -> None:
    """No commit_range -> no branch_range ref."""
    _scrub_ci_env(monkeypatch)

    from roam.evidence.env_refs import build_environment_refs

    refs = build_environment_refs(workspace_root="/repo/example")

    kinds = [r.env_kind for r in refs]
    assert "branch_range" not in kinds, (
        f"branch_range should be absent without commit_range; got {kinds}"
    )


def test_build_environment_refs_omits_branch_range_on_empty_string(
    monkeypatch,
) -> None:
    """commit_range="" is treated as absent (Pattern 2: empty = no signal)."""
    _scrub_ci_env(monkeypatch)

    from roam.evidence.env_refs import build_environment_refs

    refs = build_environment_refs(
        commit_range="",
        workspace_root="/repo/example",
    )

    kinds = [r.env_kind for r in refs]
    assert "branch_range" not in kinds, (
        f"empty commit_range should not yield a branch_range ref; got {kinds}"
    )


# ---------------------------------------------------------------------------
# Canonical ordering: ci_job first, then workspace, then branch_range,
# then local_run (CI suppresses local_run).
# ---------------------------------------------------------------------------


def test_build_environment_refs_canonical_order_in_ci(monkeypatch) -> None:
    """In a CI context with commit_range, refs are ci_job/workspace/branch_range."""
    _scrub_ci_env(monkeypatch)
    monkeypatch.setenv("GITLAB_CI", "true")
    monkeypatch.setenv("CI_JOB_ID", "gl-7")

    from roam.evidence.env_refs import build_environment_refs

    refs = build_environment_refs(
        commit_range="main..feature",
        workspace_root="/repo/example",
    )

    kinds = [r.env_kind for r in refs]
    assert kinds == ["ci_job", "workspace", "branch_range"], (
        f"unexpected ref order: {kinds}"
    )


def test_build_environment_refs_canonical_order_local(monkeypatch) -> None:
    """No CI + no commit_range -> workspace then local_run."""
    _scrub_ci_env(monkeypatch)

    from roam.evidence.env_refs import build_environment_refs

    refs = build_environment_refs(workspace_root="/repo/example")

    kinds = [r.env_kind for r in refs]
    assert kinds == ["workspace", "local_run"], (
        f"unexpected ref order: {kinds}"
    )


# ---------------------------------------------------------------------------
# env parameter override
# ---------------------------------------------------------------------------


def test_build_environment_refs_honours_explicit_env_arg(monkeypatch) -> None:
    """An explicit ``env`` mapping overrides os.environ for CI detection."""
    # Scrub real env so the explicit map is the only source.
    _scrub_ci_env(monkeypatch)

    from roam.evidence.env_refs import build_environment_refs

    fake_env = {"CIRCLECI": "true", "CIRCLE_BUILD_NUM": "circle-99"}
    refs = build_environment_refs(
        workspace_root="/repo/example",
        env=fake_env,
    )

    kinds = [r.env_kind for r in refs]
    assert "ci_job" in kinds, (
        f"explicit env should activate CI detection; got {kinds}"
    )
    ci = next(r for r in refs if r.env_kind == "ci_job")
    assert ci.env_id == "circle-99"


# ---------------------------------------------------------------------------
# Re-export from roam.evidence
# ---------------------------------------------------------------------------


def test_build_environment_refs_is_reexported_from_roam_evidence() -> None:
    """The function must be importable as ``from roam.evidence import build_environment_refs``."""
    from roam import evidence

    assert hasattr(evidence, "build_environment_refs"), (
        "build_environment_refs must be re-exported from roam.evidence"
    )
    # Same callable (not a wrapper).
    from roam.evidence.env_refs import build_environment_refs as direct

    assert evidence.build_environment_refs is direct
