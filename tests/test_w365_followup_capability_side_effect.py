"""W365-followup — logical invariant: destructive implies side_effect.

The ``@roam_capability`` decorator (``src/roam/capability.py``) exposes two
axes the Roam Review GitHub App and ``roam capabilities`` consume:

- ``destructive`` — rm-style operations.
- ``side_effect`` — "writes to disk / fires HTTP / mutates state."
  (canonical docstring: ``src/roam/capability.py:64``)

A command that is ``destructive=True`` MUST also be ``side_effect=True``: you
cannot delete data without writing to disk / mutating state. The W365 audit
surfaced two decorations (``roam_reset`` / ``roam_clean``) violating this
invariant — both flipped at W365-followup. This lint pins the invariant so a
future drive-by edit cannot silently re-introduce the contradiction.

Companion: W365 in ``tests/test_w365_tool_metadata_annotations_parity.py``
(parity between ``_TOOL_METADATA`` and ``ToolAnnotations``).
"""

from __future__ import annotations

import importlib

import pytest


def _load_full_capability_registry():
    """Force-import every cmd_*.py module so ``@roam_capability`` decorators register."""
    import roam.cli as _cli
    from roam.capability import REGISTRY

    for _cmd_name, (modpath, _attr) in _cli._COMMANDS.items():
        try:
            importlib.import_module(modpath)
        except Exception:
            # Plugin / optional modules may fail import; that's fine — we
            # only check the invariant for capabilities that DID register.
            pass
    return REGISTRY.items


def test_destructive_implies_side_effect():
    """For every @roam_capability with destructive=True, side_effect MUST be True.

    Logical invariant — you cannot delete data / mutate state without that
    being a side effect under ``capability.py:64``'s canonical semantic
    ("writes to disk / fires HTTP / mutates state").

    Drift here typically means a drive-by added ``destructive=True`` without
    auditing the ``side_effect`` axis. Fix: flip ``side_effect=True`` on the
    decoration.
    """
    caps = _load_full_capability_registry()
    violations: list[tuple[str, bool, bool]] = []
    for name, cap in caps.items():
        if bool(cap.destructive) and not bool(cap.side_effect):
            violations.append((name, bool(cap.destructive), bool(cap.side_effect)))
    assert not violations, (
        f"{len(violations)} @roam_capability decoration(s) violate the "
        f"destructive→side_effect invariant:\n"
        f"  {violations[:20]}\n\n"
        f"Each row: (capability_name, destructive, side_effect).\n"
        f"You cannot be destructive without a side effect. Either flip "
        f"side_effect=True on the decoration OR drop destructive=True if "
        f"the operation doesn't actually delete anything."
    )


@pytest.mark.parametrize("cap_name", ["reset", "clean"])
def test_w365_followup_pinned_decorations(cap_name):
    """Pin the W365-followup fix: reset / clean carry both destructive AND side_effect."""
    caps = _load_full_capability_registry()
    cap = caps.get(cap_name)
    assert cap is not None, f"capability {cap_name!r} not registered"
    assert cap.destructive is True, (
        f"{cap_name}: expected destructive=True (W365 confirmed); got {cap.destructive!r}"
    )
    assert cap.side_effect is True, (
        f"{cap_name}: expected side_effect=True (W365-followup fix); got "
        f"{cap.side_effect!r}. Deleting / mutating the index DB IS a side "
        f"effect under capability.py:64's canonical semantic."
    )
