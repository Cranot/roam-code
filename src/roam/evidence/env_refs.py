"""W266 - shared environment-ref builder for any tier-1 producer.

Background
----------

Before W266, the only producer that materialised
``EnvironmentRef`` rows was the W176 collector (used by the
``pr-replay`` recipe). The CI-detection table, hostname lookup, and
ref-assembly all lived inside
``roam.evidence.collector._build_environment_refs`` and were not
reachable from any other producer.

The W252 producer-coverage matrix flagged ``environment`` as the
most under-served evidence axis. Any tier-1 envelope that already
carries actor / authority / mode signal should also be able to
stamp the execution environment when a CI job context exists -
without inventing its own CI-detection table.

What this module is
-------------------

A small, dependency-free public API:

    from roam.evidence.env_refs import build_environment_refs

    refs = build_environment_refs(
        commit_range="abc1234..def5678",
        workspace_root="/home/alice/repos/example",
    )

Returns a tuple of :class:`EnvironmentRef` rows, in a stable order::

    1. ci_job        - when a CI provider's probe-var is truthy.
    2. workspace     - always; derived from ``workspace_root`` or cwd.
    3. branch_range  - when ``commit_range`` is provided (non-empty).
    4. local_run     - only when no CI provider was detected. The id
                       is ``socket.gethostname()``, falling back to
                       the literal ``"local"`` if hostname lookup
                       fails.

CI detection delegates to
:func:`roam.evidence.collector._detect_ci_env_id` so the
``_CI_PROVIDER_ENV_VARS`` precedence list stays the single source of
truth. The W251 cross-platform CI-detection matrix continues to pin
that behaviour; this module piggy-backs on it.

Why a NEW module rather than moving the existing helper
-------------------------------------------------------

The pre-existing ``_build_environment_refs`` in ``collector.py`` has a
pr-bundle-envelope-coupled signature::

    (pr_bundle_envelope, caller_repo_id, caller_git_range,
     caller_commit_sha) -> tuple[EnvironmentRef, ...]

Moving it would either break that signature or force every caller in
``collect_change_evidence`` to keep passing a four-arg shape that
producers outside the collector don't have. The W266 ticket explicitly
allowed the delegator pattern when extraction would touch >3 sites; we
take it here. The collector helper stays intact (so the v0/v1
content-hash contract on existing packets remains byte-stable), and
the new shared helper is the public API for everyone else.

Public surface
--------------

Re-exported from :mod:`roam.evidence`:

* :func:`build_environment_refs` - the headline function.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Mapping

from roam.evidence.refs import EnvironmentRef


def build_environment_refs(
    *,
    commit_range: str | None = None,
    workspace_root: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[EnvironmentRef, ...]:
    """Materialise environment refs for any tier-1 producer.

    Parameters
    ----------
    commit_range
        Optional git range / commit-sha string. When non-empty, a
        ``branch_range`` ref is emitted with this exact value as the
        ``env_id``. Producers should pass whatever git identifier
        scopes their change (e.g. ``"main..feature"``, a single
        commit sha, or a tag range).
    workspace_root
        Optional explicit workspace path. When omitted, ``os.getcwd()``
        is used. Resolved to an absolute string so two refs from
        different working directories are distinct.
    env
        Optional mapping to use in place of ``os.environ`` for CI
        detection. Test-only; production callers pass nothing and get
        the live process environment.

    Returns
    -------
    tuple[EnvironmentRef, ...]
        Refs in this canonical order: ``ci_job`` (when detected),
        ``workspace`` (always), ``branch_range`` (when commit_range
        provided), ``local_run`` (only when no CI detected). Empty
        keys / falsy values are skipped silently - the caller never
        has to filter the result.

    Notes
    -----
    * The function is total: it never raises. Hostname lookup falls
      back to the literal string ``"local"`` if the OS refuses to
      answer; cwd resolution falls back to ``"unknown"`` if even
      :func:`os.getcwd` fails (rare on Windows after a deleted-cwd
      race).
    * Duplicate ``(env_kind, env_id)`` pairs cannot occur given the
      ordered, single-source construction; no de-dup pass is needed.
    """
    # Delegate CI detection to the collector helper so the
    # ``_CI_PROVIDER_ENV_VARS`` table stays the single source of truth.
    # Local import avoids a circular dep between env_refs.py and
    # collector.py.
    from roam.evidence.collector import _detect_ci_env_id

    refs: list[EnvironmentRef] = []

    ci_env_id = _detect_ci_env_id(env)
    if ci_env_id:
        refs.append(EnvironmentRef(env_kind="ci_job", env_id=ci_env_id))

    # workspace ref - always present. Resolve to an absolute string so
    # two refs from different working directories don't accidentally
    # collide.
    if workspace_root is None:
        try:
            workspace_id = os.getcwd()
        except OSError:
            workspace_id = "unknown"
    else:
        workspace_id = os.fspath(workspace_root)
    if workspace_id:
        refs.append(EnvironmentRef(env_kind="workspace", env_id=workspace_id))

    if isinstance(commit_range, str) and commit_range:
        refs.append(EnvironmentRef(env_kind="branch_range", env_id=commit_range))

    if ci_env_id is None:
        try:
            hostname = socket.gethostname() or "local"
        except OSError:
            hostname = "local"
        refs.append(EnvironmentRef(env_kind="local_run", env_id=hostname))

    return tuple(refs)


__all__ = ["build_environment_refs"]
