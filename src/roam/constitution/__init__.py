"""Repo-local agent constitution (R24).

Single declarative file that points an agent at every agent-OS
substrate file the repo has — ``AGENTS.md``, ``roam-laws.yml``,
``.roam/rules/*.yml``, ``.roam/memory.jsonl`` — plus the required
checks an agent must run at each workflow gate and the policy
thresholds that govern blast radius, cycles, and test coverage.

The constitution is intentionally a *capstone*: it does not own or
extend any of the substrates it references. Each substrate is built
and maintained by its dedicated wave (W6 memory, W7 runs, W8.1 rules,
W8.3 laws). The constitution gives them a single discovery point.

Top-level API (see :mod:`roam.constitution.loader`)::

    load_constitution(repo_root)   -> Optional[Constitution]
    init_constitution(repo_root)   -> Path
    check_constitution(repo_root, constitution) -> CheckReport
    assess_constitution_upgrade(constitution) -> ModePolicyUpgradeReport
    upgrade_constitution(repo_root) -> ModePolicyUpgradeReport
    apply_constitution(repo_root, constitution, gate=...) -> ApplyReport
"""

from __future__ import annotations

from roam.constitution.loader import (
    ApplyReport,
    CheckReport,
    Constitution,
    ConstitutionConcurrentUpdate,
    ConstitutionUpgradePreviewMismatch,
    ConstitutionUpgradeRequiresAcceptance,
    ModePolicyUpgradeReport,
    apply_constitution,
    assess_constitution_upgrade,
    check_constitution,
    constitution_path,
    init_constitution,
    load_constitution,
    mode_policy_digest,
    upgrade_constitution,
)

__all__ = [
    "ApplyReport",
    "CheckReport",
    "Constitution",
    "ConstitutionConcurrentUpdate",
    "ConstitutionUpgradePreviewMismatch",
    "ConstitutionUpgradeRequiresAcceptance",
    "ModePolicyUpgradeReport",
    "apply_constitution",
    "assess_constitution_upgrade",
    "check_constitution",
    "constitution_path",
    "init_constitution",
    "load_constitution",
    "mode_policy_digest",
    "upgrade_constitution",
]
