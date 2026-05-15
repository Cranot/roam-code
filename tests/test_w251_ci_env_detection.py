"""W251 - cross-platform CI env detection matrix.

Pins the behaviour of
:func:`roam.evidence.collector._detect_ci_env_id` and the
``_CI_PROVIDER_ENV_VARS`` precedence list across all six supported
providers + the generic ``CI=true`` fallback + the no-CI case.

The function's actual contract (W190):

* Signature: ``_detect_ci_env_id(env: Mapping[str, str] | None = None) -> str | None``
* Returns: the value of the first non-empty value-var for the first
  provider whose probe-var is truthy, in the order declared by
  ``_CI_PROVIDER_ENV_VARS``. When the probe-var is truthy but the
  paired value-var is unset, returns ``f"{probe_var.lower()}:unknown"``
  so callers always get a non-empty string. Returns ``None`` only when
  NO provider's probe-var is truthy.

The prompt's test sketch assumed a ``(env_kind, env_id)`` tuple return
shape; the real function returns a bare ``str | None``. Tests below
adapt to the real shape (LAW 11: pin existing behaviour rather than
break it). The ``env_kind`` for any non-None result is always
``"ci_job"`` per ``_build_environment_refs`` callers, so that axis is
not represented in the function's own return value.
"""

from __future__ import annotations

import pytest


# All CI env vars the collector probes - scrubbed from os.environ before
# every test that exercises the no-arg path so the suite is deterministic
# regardless of where it runs (developer laptop vs GitHub Actions vs CI).
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
    """Unset every CI-detection env var so the helper sees a clean env."""
    for var in _CI_ENV_VARS_TO_SCRUB:
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# Parametrized: 6 providers + generic CI + no-CI = 8 cases
# ---------------------------------------------------------------------------
#
# expected_env_id is the EXACT string ``_detect_ci_env_id`` must return.
# None means the function returns None (no CI detected at all).

CI_TEST_CASES: list[tuple[str, dict[str, str], str | None]] = [
    (
        "github_actions",
        {"GITHUB_ACTIONS": "true", "GITHUB_RUN_ID": "42"},
        "42",
    ),
    (
        "gitlab_ci",
        {"GITLAB_CI": "true", "CI_JOB_ID": "gl-42"},
        "gl-42",
    ),
    (
        "buildkite",
        {"BUILDKITE": "true", "BUILDKITE_BUILD_ID": "bk-42"},
        "bk-42",
    ),
    (
        "circleci",
        {"CIRCLECI": "true", "CIRCLE_BUILD_NUM": "100"},
        "100",
    ),
    (
        "jenkins",
        {"JENKINS_URL": "https://jenkins.local", "BUILD_TAG": "jenkins-42"},
        "jenkins-42",
    ),
    (
        "azure_pipelines",
        {"TF_BUILD": "True", "BUILD_BUILDID": "az-42"},
        "az-42",
    ),
    (
        "generic_ci_only",
        {"CI": "true", "CI_JOB_ID": "generic-42"},
        "generic-42",
    ),
    (
        "no_ci",
        {},
        None,
    ),
]


@pytest.mark.parametrize(
    "name,env,expected_env_id",
    CI_TEST_CASES,
    ids=[c[0] for c in CI_TEST_CASES],
)
def test_ci_provider_detection(
    name: str,
    env: dict[str, str],
    expected_env_id: str | None,
    monkeypatch,
) -> None:
    """W251: each CI provider's env vars map to the correct env_id.

    Pass the env dict directly to the function rather than relying on
    monkeypatch + os.environ so the test is hermetic. (The function
    accepts an explicit ``env`` parameter precisely for this purpose.)
    """
    _scrub_ci_env(monkeypatch)
    from roam.evidence.collector import _detect_ci_env_id

    result = _detect_ci_env_id(env)

    if expected_env_id is None:
        assert result is None, (
            f"{name}: expected no CI detection, got {result!r}"
        )
    else:
        assert result == expected_env_id, (
            f"{name}: expected env_id={expected_env_id!r}, got {result!r}"
        )


# ---------------------------------------------------------------------------
# Named edge cases - 3 specific behaviours worth pinning standalone
# ---------------------------------------------------------------------------


def test_falsy_ci_value_treated_as_not_in_ci(monkeypatch) -> None:
    """CI=false / 0 / '' / 'False' MUST NOT trigger CI detection.

    The function's contract: probe values in ``{"false", "0", ""}``
    (case-insensitive) skip the provider. This pins the contract on
    the generic ``CI`` fallback - the most commonly mis-set var.
    """
    _scrub_ci_env(monkeypatch)
    from roam.evidence.collector import _detect_ci_env_id

    for value in ("false", "0", "", "False", "FALSE"):
        env = {"CI": value}
        result = _detect_ci_env_id(env)
        assert result is None, (
            f"CI={value!r} should not be detected as CI, got {result!r}"
        )


def test_provider_precedence_github_actions_over_generic_ci(monkeypatch) -> None:
    """Both GITHUB_ACTIONS=true AND CI=true set -> GitHub-specific wins.

    Pins the ``_CI_PROVIDER_ENV_VARS`` declaration order: the
    most-specific provider (GitHub Actions, declared first) preempts
    the generic ``CI`` fallback (declared last) even when both probes
    are truthy.
    """
    _scrub_ci_env(monkeypatch)
    from roam.evidence.collector import _detect_ci_env_id

    env = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_RUN_ID": "gh-42",
        "CI": "true",
        "CI_JOB_ID": "generic-42",
    }
    result = _detect_ci_env_id(env)
    assert result == "gh-42", (
        f"GitHub Actions should win over generic CI; got {result!r}"
    )


def test_provider_with_missing_id_env_var_falls_back(monkeypatch) -> None:
    """GITHUB_ACTIONS=true but GITHUB_RUN_ID unset -> synthetic fallback id.

    Pins the W190 design: env_id MUST be a non-empty string when any
    provider's probe-var is truthy, so the collector synthesises
    ``"<probe_var_lower>:unknown"`` when the paired value-var is
    absent. This guarantees an EnvironmentRef with env_kind="ci_job"
    can always be constructed once we know we're in CI.
    """
    _scrub_ci_env(monkeypatch)
    from roam.evidence.collector import _detect_ci_env_id

    env = {"GITHUB_ACTIONS": "true"}  # GITHUB_RUN_ID deliberately absent
    result = _detect_ci_env_id(env)
    assert result is not None, (
        "Truthy provider probe should always yield a non-empty env_id"
    )
    assert isinstance(result, str) and result, (
        f"env_id must be a non-empty string, got {result!r}"
    )
    # The exact fallback shape is ``"<probe_var_lower>:unknown"`` -
    # pin it so producer-side EnvironmentRef stringification stays
    # stable across releases.
    assert result == "github_actions:unknown", (
        f"unexpected fallback shape: {result!r}"
    )


# ---------------------------------------------------------------------------
# Bonus: pin the _CI_PROVIDER_ENV_VARS precedence list itself
# ---------------------------------------------------------------------------
#
# The function's correctness depends on the declared order of
# ``_CI_PROVIDER_ENV_VARS``. If a future refactor sorts that list or
# adds a provider in the middle, the precedence tests above might
# still pass while the registry itself silently drifts. Pin the exact
# tuple shape so drift is caught at the module-constant level.


def test_ci_provider_env_vars_registry_order() -> None:
    """The declared provider order is part of the public contract.

    Order matters because the function returns the FIRST match. Any
    change to this list (adding a provider, reordering) MUST be a
    deliberate decision tracked here.
    """
    from roam.evidence.collector import _CI_PROVIDER_ENV_VARS

    expected = (
        ("GITHUB_ACTIONS", "GITHUB_RUN_ID"),
        ("GITLAB_CI", "CI_JOB_ID"),
        ("BUILDKITE", "BUILDKITE_BUILD_ID"),
        ("CIRCLECI", "CIRCLE_BUILD_NUM"),
        ("JENKINS_URL", "BUILD_TAG"),
        ("TF_BUILD", "BUILD_BUILDID"),
        ("CI", "CI_JOB_ID"),
    )
    assert _CI_PROVIDER_ENV_VARS == expected, (
        "CI provider registry drifted - update this test deliberately. "
        f"got: {_CI_PROVIDER_ENV_VARS}"
    )
