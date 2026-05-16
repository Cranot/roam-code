"""Drift guard: keep cli._SARIF_CONSUMERS in sync with reality.

W22.3 caught a silent drift where the `--sarif` help text named 7 consumers
but 14 commands actually honoured the flag. The fix landed a closed,
alphabetically-sorted enumeration at `roam.cli._SARIF_CONSUMERS` that the
help-string formatters interpolate, and this test makes the enumeration
self-healing: any consumer added (or removed) without updating the tuple
fails the build.

What "consumes --sarif" means here: the command module references
``ctx.obj["sarif"]``, ``ctx.obj.get("sarif")``, or the parameter name
``sarif_mode`` — the three vehicles by which the global flag reaches a
subcommand. A simple substring scan of the source text is sufficient
(and dodges the cost of importing every command module just for a lint).

Per CLAUDE.md Constraint 8: closed enumeration > free string composition.
The test fails the build on:
  - a new SARIF consumer ships without being added to `_SARIF_CONSUMERS`
  - a command is removed from `_SARIF_CONSUMERS` but still honours --sarif
  - a command is in `_SARIF_CONSUMERS` but no longer consumes the flag
"""

from __future__ import annotations

from pathlib import Path

import pytest

from roam.cli import _COMMANDS, _DEPRECATED_COMMANDS, _SARIF_CONSUMERS

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Substrings that signal a module actually consumes the global --sarif flag.
# We avoid AST import (too slow on every CI run, and would force importing
# 200+ command modules which network-walks the language registry). The
# substring approach is robust enough — no false positives observed across
# the existing 14 consumers; comments mentioning "--sarif" in cmd_health.py
# don't include these tokens.
_SARIF_CONSUMER_MARKERS: tuple[str, ...] = (
    'ctx.obj["sarif"]',
    "ctx.obj['sarif']",
    'ctx.obj.get("sarif")',
    "ctx.obj.get('sarif')",
    "sarif_mode",
)


def _commands_dir() -> Path:
    """Return the absolute path to src/roam/commands/."""
    # tests/ sits next to src/, so up-1 then down to src/roam/commands.
    return Path(__file__).resolve().parent.parent / "src" / "roam" / "commands"


def _module_consumes_sarif(path: Path) -> bool:
    """Return True if the module body references any sarif-consumer marker."""
    text = path.read_text(encoding="utf-8")
    return any(marker in text for marker in _SARIF_CONSUMER_MARKERS)


def _module_path_to_canonical_cli_name(module_path: str) -> str | None:
    """Reverse-look-up the canonical CLI name for a `cmd_*.py` module.

    A single module may back multiple registry entries (canonical + aliases);
    we return the FIRST non-deprecated name, which is the one users should
    see in help text. Returns None if the module isn't registered.
    """
    candidates: list[str] = []
    for cli_name, (mod, _attr) in _COMMANDS.items():
        if mod == module_path:
            candidates.append(cli_name)
    # Prefer the non-deprecated alias (canonical name) for help-text honesty.
    for name in candidates:
        if name not in _DEPRECATED_COMMANDS:
            return name
    # Fallback: every match is deprecated — return the first (lets the test
    # surface a sensible error message instead of silently dropping a
    # consumer).
    return candidates[0] if candidates else None


def _discovered_canonical_sarif_consumers() -> set[str]:
    """Scan `src/roam/commands/cmd_*.py` and return canonical CLI names."""
    discovered: set[str] = set()
    cmd_dir = _commands_dir()
    assert cmd_dir.is_dir(), f"commands dir not found: {cmd_dir}"

    for path in sorted(cmd_dir.glob("cmd_*.py")):
        if not _module_consumes_sarif(path):
            continue
        # cmd_check_rules.py -> roam.commands.cmd_check_rules
        module_path = f"roam.commands.{path.stem}"
        cli_name = _module_path_to_canonical_cli_name(module_path)
        if cli_name is None:
            pytest.fail(
                f"Module {module_path} consumes --sarif but is not registered "
                f"in cli._COMMANDS. Either register it or remove the --sarif "
                f"handling.",
            )
        discovered.add(cli_name)
    return discovered


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_sarif_consumers_tuple_is_sorted_alphabetically() -> None:
    """The enumeration must be in deterministic (alphabetic) order.

    Help-text output is user-visible, so a stable order keeps diffs small
    and grep-output predictable.
    """
    assert list(_SARIF_CONSUMERS) == sorted(_SARIF_CONSUMERS), (
        f"_SARIF_CONSUMERS must be sorted alphabetically — got {list(_SARIF_CONSUMERS)}"
    )


def test_sarif_consumers_tuple_has_no_duplicates() -> None:
    """Every entry must be unique — duplicates would inflate help text."""
    assert len(_SARIF_CONSUMERS) == len(set(_SARIF_CONSUMERS)), f"_SARIF_CONSUMERS has duplicates: {_SARIF_CONSUMERS}"


def test_sarif_consumers_tuple_only_lists_registered_commands() -> None:
    """Every entry must be a real, currently-registered CLI command.

    Catches the case where a command is renamed or removed without updating
    `_SARIF_CONSUMERS` — the help text would advertise a phantom command.
    """
    unknown = [name for name in _SARIF_CONSUMERS if name not in _COMMANDS]
    assert not unknown, f"_SARIF_CONSUMERS lists commands not in cli._COMMANDS: {unknown}"


def test_sarif_consumers_tuple_lists_no_deprecated_aliases() -> None:
    """Help text should advertise canonical names, not deprecated aliases.

    A deprecated alias still routes correctly, but showing it in help
    sends new users down the deprecation path.
    """
    deprecated = [name for name in _SARIF_CONSUMERS if name in _DEPRECATED_COMMANDS]
    assert not deprecated, f"_SARIF_CONSUMERS includes deprecated aliases (use canonical names): {deprecated}"


def test_sarif_consumers_tuple_matches_actual_consumers() -> None:
    """The headline drift guard: tuple must equal the discovered set.

    Fails on:
      - new --sarif consumer added without updating `_SARIF_CONSUMERS`
      - command removed from `_SARIF_CONSUMERS` but still consumes --sarif
      - command in `_SARIF_CONSUMERS` but no longer consumes --sarif
    """
    expected = set(_SARIF_CONSUMERS)
    actual = _discovered_canonical_sarif_consumers()

    missing_from_tuple = actual - expected
    extra_in_tuple = expected - actual

    msg_parts: list[str] = []
    if missing_from_tuple:
        msg_parts.append(
            "Commands consume --sarif but are NOT listed in "
            f"_SARIF_CONSUMERS: {sorted(missing_from_tuple)}. "
            "Add them (alphabetically) to src/roam/cli.py:_SARIF_CONSUMERS.",
        )
    if extra_in_tuple:
        msg_parts.append(
            "Commands listed in _SARIF_CONSUMERS but no longer "
            f"consume --sarif: {sorted(extra_in_tuple)}. "
            "Remove them from src/roam/cli.py:_SARIF_CONSUMERS.",
        )
    assert not msg_parts, "\n\n".join(msg_parts)


def test_sarif_consumers_count_is_thirty_seven() -> None:
    """Spot-check the count W22.3 audited + W1146 + W1165 + W1160 + W1159
    + W1171 + W1172 + W1192 + W1195 + W1203 + W1208 + W1218 + W1217
    + W1219 + W1215 + W1216 + W1209 + W1213 + W1207 + W1211 + W1210
    + W1226 + W1227 + W1229.

    If a 38th consumer is added intentionally this assertion needs to be
    bumped — that's deliberate friction to force the author to confirm the
    count actually changed (and re-run the audit). If it drops to 36 the
    same applies in reverse.
    """
    assert len(_SARIF_CONSUMERS) == 37, (
        f"_SARIF_CONSUMERS has {len(_SARIF_CONSUMERS)} entries; "
        "W22.3 audited 14, W1146 added critique = 15, W1165 added "
        "impact = 16, W1160 added affected-tests = 17, W1159 added "
        "partition = 18, W1171 added smells = 19, W1172 added "
        "clones = 20, W1192 added delete-check = 21, W1195 added "
        "auth-gaps = 22, W1203 added test-impact = 23, W1208 added "
        "n1 = 24, W1218 added orphan-imports = 25, W1217 added "
        "missing-index = 26, W1219 added over-fetch = 27, W1215 "
        "added bus-factor = 28, W1216 added laws = 29, W1209 added "
        "fan = 30, W1213 added duplicates = 31, W1207 added "
        "llm-smells = 32, W1211 added dark-matter = 33, W1210 "
        "added hotspots = 34, W1226 added flag-dead = 35, W1227 "
        "added orphan-routes = 36, W1229 added verify-imports = 37. "
        "If the count changed intentionally, bump this assertion "
        "and re-audit the help text."
    )


def test_action_yml_supported_sarif_subset_of_cli_consumers() -> None:
    """Drift guard: every action.yml _SUPPORTED_SARIF entry must be in
    cli.py _SARIF_CONSUMERS. Otherwise the GitHub Action will attempt
    to read --sarif output from a command that doesn't expose the flag.
    See W1158 audit + W1167 subset-intent comment in action.yml.
    """
    import re

    action_yml = (Path(__file__).resolve().parent.parent / "action.yml").read_text(encoding="utf-8")
    # Match: _SUPPORTED_SARIF="health dead complexity rules secrets algo critique"
    match = re.search(r'_SUPPORTED_SARIF="([^"]+)"', action_yml)
    assert match, "Could not find _SUPPORTED_SARIF literal in action.yml"
    action_commands = set(match.group(1).split())

    # Note: action.yml uses dash-form (e.g. "py-modern"), as does cli.py
    # _SARIF_CONSUMERS. No transformation needed.
    extra = action_commands - set(_SARIF_CONSUMERS)
    assert not extra, (
        f"action.yml _SUPPORTED_SARIF has entries NOT in cli.py "
        f"_SARIF_CONSUMERS: {sorted(extra)}. Either add them to "
        f"_SARIF_CONSUMERS (and ensure --sarif flag works) or remove "
        f"from action.yml. See W1158 / W1168."
    )
