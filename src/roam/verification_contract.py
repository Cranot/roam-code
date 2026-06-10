"""verification_contract — G3 of the Roam Guard MVP.

Given:
  * changed_files  : list[str] from `roam diff` or git
  * command_graph  : output of `roam.command_graph.build_command_graph`
  * risk           : {"level": "low|medium|high", "reasons": [...], "paths": [...]}
  * mode           : "read_only" | "safe_edit" | "migration" | "autonomous_pr"
  * policy_profile : "startup" | "regulated" | None (default startup)

Produces:
  {
    "required": [{"command": "...", "kind": "test", "reason": "..."}, ...],
    "skipped":  [{"command": "...", "reason": "..."}, ...]
  }

Rules are evidence-backed and deterministic. Every `reason` string maps to a
machine-readable code so the verdict engine can act on it.

Architecture-seal mapping:
  command_graph        →  what CAN be run        (facts, SHIPPED — G2)
  verification_contract →  what MUST run for THIS change (judgment, this module)
  proof_bundle         →  what actually ran + verdict (evidence + verdict — cmd_pr_bundle)

Per the pivot memo (`project_pivot_to_roam_guard`), this is the gap between
the G2 command_graph and the AgentChangeProofBundle v1 schema.
"""

from __future__ import annotations

from typing import Any

from roam.guard_rules import FilePatternRule, RulePack

# ---- reason codes (closed enum — every required/skipped reason ships one) ----
# Built-in codes from the default RulePack. Custom RulePacks can add new
# `id` values (which become reason codes); those are validated by the rule-pack
# loader, not against this set.

RULE_REASON_CODES = frozenset(
    {
        # required reasons — built-in default pack
        "auth_file_changed",
        "migration_file_changed",
        "public_api_changed",
        "config_file_changed",
        "high_risk_path",
        "policy_floor",
        "matching_test_kind",
        "test_file_changed",
        "default_test_required",
        # skipped reasons
        "no_matching_file_kind",
        "no_public_api_changed",
        "no_risk_match",
        "kind_not_test",
        "skipped_by_mode",
    }
)

# Back-compat alias — keep `REASON_CODES` as a name so existing callers don't
# break. The canonical name is RULE_REASON_CODES (W33b): these are RULE-MATCH
# codes, semantically distinct from `guard_enums.REASON_CODES` which is the
# VERDICT-OUTCOME closed enum. Both lists used to be named `REASON_CODES` and
# shared one literal (`high_risk_path`), so code that imported either could
# pass membership checks intended for the other — silent footgun.
REASON_CODES = RULE_REASON_CODES


def _file_matches(file: str, rules: tuple[FilePatternRule, ...]) -> str | None:
    """Return the reason_code for the first rule whose regex matches, or None."""
    for rule in rules:
        if rule.regex.search(file):
            return rule.id
    return None


def _kind_applies_to_file(file: str, kind: str, rules: tuple[FilePatternRule, ...]) -> bool:
    """Does this kind of command apply to changes in this file?"""
    for rule in rules:
        if rule.regex.search(file) and kind in rule.applies_to_kinds:
            return True
    return False


def build_verification_contract(
    *,
    changed_files: list[str],
    command_graph: dict[str, Any],
    risk: dict[str, Any] | None = None,
    mode: str = "safe_edit",
    policy_profile: str = "startup",
    rule_pack: RulePack | None = None,
) -> dict[str, Any]:
    """Produce a verification_contract for THIS diff.

    Args:
      changed_files: paths relative to repo root.
      command_graph: from `roam.command_graph.build_command_graph`.
                     Expected shape: {"commands": [{"name", "kind", "invocation", ...}, ...]}
      risk: {"level": "low|medium|high", "reasons": [...], "paths": [...]}
      mode: agent's declared operating mode.
      policy_profile: which policy floor applies.
      rule_pack: optional RulePack override. Defaults to the built-in pack
                 (matches the legacy hard-coded rules). Custom packs are
                 loaded via `roam.guard_rules.load_rule_pack`.

    Returns:
      {"required": [...], "skipped": [...]}
      Each entry is {"command", "kind", "invocation"?, "reason"} (required)
      or {"command", "reason"} (skipped).
    """
    risk = risk or {"level": "low", "reasons": [], "paths": []}
    risk_level = risk.get("level", "low")
    rules = rule_pack or RulePack.default()
    file_pattern_rules = rules.file_patterns

    commands: list[dict[str, Any]] = command_graph.get("commands", []) or []

    required: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    # Collect "why this changed file matters" upfront — one reason per file.
    file_reasons: list[tuple[str, str]] = []
    for f in changed_files:
        reason = _file_matches(f, file_pattern_rules)
        if reason:
            file_reasons.append((f, reason))

    high_risk_paths = set(risk.get("paths") or [])
    has_high_risk = risk_level == "high" or bool(high_risk_paths & set(changed_files))

    # Iterate each available command, decide required vs skipped.
    for cmd in commands:
        # command_graph uses "command" (the invocation string) and "id" (stable key).
        name = cmd.get("id") or cmd.get("command") or cmd.get("name") or ""
        kind = cmd.get("kind", "")
        invocation = cmd.get("command") or cmd.get("invocation", name)

        if not kind:
            skipped.append({"command": name, "reason": "kind_not_test"})
            continue

        # High-risk path → all test commands required regardless of file match.
        if has_high_risk and kind == "test":
            required.append(
                {
                    "command": name,
                    "kind": kind,
                    "invocation": invocation,
                    "reason": "high_risk_path",
                    "detail": list(high_risk_paths & set(changed_files)) or risk.get("reasons", []),
                }
            )
            continue

        # Look for file-kind matches.
        matched: list[tuple[str, str]] = [
            (f, reason) for f, reason in file_reasons if _kind_applies_to_file(f, kind, file_pattern_rules)
        ]
        if matched:
            # Pick the first matched reason (most-specific pattern fires first).
            f, reason_code = matched[0]
            required.append(
                {
                    "command": name,
                    "kind": kind,
                    "invocation": invocation,
                    "reason": reason_code,
                    "detail": [f],
                }
            )
            continue

        # Policy floor — regulated profile requires tests on EVERY change.
        if policy_profile == "regulated" and kind == "test" and changed_files:
            required.append(
                {
                    "command": name,
                    "kind": kind,
                    "invocation": invocation,
                    "reason": "policy_floor",
                    "detail": ["regulated profile: tests required on all changes"],
                }
            )
            continue

        # No reason to require this command.
        if kind == "test":
            reason = "no_matching_file_kind" if file_reasons else "no_risk_match"
        else:
            reason = "kind_not_test"
        skipped.append({"command": name, "reason": reason})

    return {
        "required": required,
        "skipped": skipped,
        "_meta": {
            "changed_files_count": len(changed_files),
            "high_risk_path_hits": list(high_risk_paths & set(changed_files)),
            "mode": mode,
            "policy_profile": policy_profile,
        },
    }
