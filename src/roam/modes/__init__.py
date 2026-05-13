"""Agent-mode policy substrate (R16).

Defines the four agent modes (``read_only`` / ``safe_edit`` / ``migration`` /
``autonomous_pr``) and resolves the active mode for a repo. Mode policies
are *materialised allow-lists* of bare roam command names; a higher mode
is a strict superset of every lower mode (cumulative semantics).

This module is **SUBSTRATE ONLY** — it answers the question "is command X
allowed in the active mode?" but does NOT auto-enforce gating at the CLI
dispatch level. Wiring `check_command_allowed()` into every command's
entry point is an explicit follow-up step (see BACKLOG R16, section 4).

The constitution loader is the canonical source of mode allow-lists when
``.roam/constitution.yml`` declares a ``modes:`` block. When no
constitution exists (or its ``modes`` block is empty), we fall back to
``DEFAULT_MODE_POLICIES`` baked into :mod:`roam.modes.policy`.

Public surface re-exported here::

    VALID_MODES                 tuple of canonical mode names
    DEFAULT_MODE                "safe_edit"
    ModePolicy                  dataclass with .name and .allowed_commands
    resolve_mode()              resolve the active mode
    check_command_allowed()     (allowed, reason) for a command in a mode
    set_active_mode()           persist .roam/active_mode
    get_active_mode()           read .roam/active_mode (or None)
    list_modes()                list every valid mode + its policy
"""

from __future__ import annotations

from roam.modes.policy import (
    DEFAULT_MODE,
    DEFAULT_MODE_POLICIES,
    VALID_MODES,
    ModePolicy,
    check_command_allowed,
    get_active_mode,
    list_modes,
    resolve_mode,
    set_active_mode,
)

__all__ = [
    "DEFAULT_MODE",
    "DEFAULT_MODE_POLICIES",
    "VALID_MODES",
    "ModePolicy",
    "check_command_allowed",
    "get_active_mode",
    "list_modes",
    "resolve_mode",
    "set_active_mode",
]
