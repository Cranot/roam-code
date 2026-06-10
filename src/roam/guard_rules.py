"""Pluggable rule packs for verification_contract.

A `RulePack` is a YAML-defined set of file-pattern → required-check rules.
Roam Guard ships with a default pack matching the current hard-coded
patterns (auth / migrations / public-API / config / test files). Users
override via `roam guard-pr --rules path/to/pack.yml`.

YAML shape:
```yaml
name: python-default
version: 1.0

file_patterns:
  - id: auth_file_changed
    regex: '(?:^|/)auth[/_]|...'
    applies_to_kinds: [test]
  - id: migration_file_changed
    regex: '/(?:migrations?|migrate)/'
    applies_to_kinds: [test, migration]
```

`id` becomes the verdict reason code. `applies_to_kinds` matches against
command_graph entries' `kind` field.

Architecture:
  * `RulePack`   — frozen dataclass
  * `FilePatternRule` — one regex + kinds-set
  * `RulePack.default()` — the built-in pack matching legacy hard-coded rules
  * `load_rule_pack(path)` — parses a YAML file
  * `get_active_rules(path|None)` — returns custom-or-default
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FilePatternRule:
    """One pattern rule. `id` is also the verdict reason code."""

    id: str
    regex: re.Pattern[str]
    applies_to_kinds: frozenset[str]


@dataclass(frozen=True)
class RulePack:
    """A named bundle of file-pattern rules."""

    name: str
    version: str
    file_patterns: tuple[FilePatternRule, ...]

    @classmethod
    def default(cls) -> "RulePack":
        """Built-in pack — matches the hard-coded rules in pre-Phase-5 code."""
        return cls(
            name="default",
            version="1.0",
            file_patterns=(
                FilePatternRule(
                    id="auth_file_changed",
                    regex=re.compile(
                        r"(?:^|/)auth[/_]|(?:^|/)(?:sessions?|login|jwt|oauth|sanctum)\.|/middleware/auth\b",
                        re.IGNORECASE,
                    ),
                    applies_to_kinds=frozenset({"test"}),
                ),
                FilePatternRule(
                    id="migration_file_changed",
                    regex=re.compile(
                        r"/(?:migrations?|migrate)/|\.migration\.|database/migrations/",
                        re.IGNORECASE,
                    ),
                    applies_to_kinds=frozenset({"test", "migration"}),
                ),
                FilePatternRule(
                    id="public_api_changed",
                    regex=re.compile(r"^(?:src/.*\.py|src/.*\.ts|lib/.*\.rb|app/.*\.php)$"),
                    applies_to_kinds=frozenset({"test"}),
                ),
                FilePatternRule(
                    id="config_file_changed",
                    regex=re.compile(r"\.(?:env|toml|yaml|yml|json)$|^pyproject\.toml$|^package\.json$|^Cargo\.toml$"),
                    applies_to_kinds=frozenset({"test", "lint"}),
                ),
                FilePatternRule(
                    id="test_file_changed",
                    regex=re.compile(r"(?:^|/)(?:tests?|spec|__tests__)(?:/|$)|_test\.|\.test\.|\.spec\."),
                    applies_to_kinds=frozenset({"test"}),
                ),
            ),
        )

    def matches_path(self, path: str) -> list[FilePatternRule]:
        """Return every rule in this pack whose regex matches `path`.

        Public API (W25) — replaces the prior private `_match_rules`
        helper in cmd_guard_rules.py. Use this when you need to know
        which rules a file would trigger without invoking the full
        verification-contract pipeline.
        """
        return [r for r in self.file_patterns if r.regex.search(path)]

    def to_dict(self) -> dict[str, Any]:
        """Serializable shape — useful for YAML export."""
        return {
            "name": self.name,
            "version": self.version,
            "file_patterns": [
                {
                    "id": r.id,
                    "regex": r.regex.pattern,
                    "applies_to_kinds": sorted(r.applies_to_kinds),
                }
                for r in self.file_patterns
            ],
        }


def load_rule_pack(path: Path) -> RulePack:
    """Load a rule pack from a YAML file.

    Raises ValueError on malformed YAML or missing required keys.
    Resolves `extends:` directives against built-in packs by name.
    """
    import yaml  # type: ignore  # unguarded-import: ok

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError) as e:
        raise ValueError(f"failed to load rule pack at {path}: {e}") from e
    return _parse_rule_pack(data, source=str(path))


def parse_rule_pack_dict(data: dict[str, Any]) -> RulePack:
    """Parse a pre-loaded dict (e.g. from inline YAML or programmatic use)."""
    return _parse_rule_pack(data, source="<dict>")


# Built-in pack registry — looked up when a custom pack uses `extends: <name>`.
_BUILTIN_PACKS: dict[str, "RulePack"] = {}


def _builtin_pack(name: str) -> "RulePack | None":
    """Resolve a built-in pack by name. Lazily populated."""
    if name not in _BUILTIN_PACKS:
        if name == "default":
            _BUILTIN_PACKS["default"] = RulePack.default()
        else:
            return None
    return _BUILTIN_PACKS[name]


def _merge_packs(
    base: RulePack, override_name: str, override_version: str, override_rules: tuple[FilePatternRule, ...]
) -> RulePack:
    """Merge two packs. Rules with the same id in `override_rules` replace
    the corresponding rule in `base`; new ids append. Preserves order:
    base rules first, then any new overrides appended at the end.
    """
    override_by_id = {r.id: r for r in override_rules}
    merged: list[FilePatternRule] = []
    for rule in base.file_patterns:
        merged.append(override_by_id.get(rule.id, rule))
    for rule in override_rules:
        if rule.id not in {r.id for r in base.file_patterns}:
            merged.append(rule)
    return RulePack(
        name=override_name,
        version=override_version,
        file_patterns=tuple(merged),
    )


def _parse_rule_pack(data: Any, source: str) -> RulePack:
    if not isinstance(data, dict):
        raise ValueError(f"rule pack {source} must be a mapping, got {type(data).__name__}")
    name = data.get("name")
    version = str(data.get("version", "1.0"))
    extends = data.get("extends")
    file_patterns_raw = data.get("file_patterns") or []
    if not isinstance(name, str) or not name:
        raise ValueError(f"rule pack {source} missing required field `name`")
    if not isinstance(file_patterns_raw, list):
        raise ValueError(f"rule pack {source}: `file_patterns` must be a list")

    rules: list[FilePatternRule] = []
    for i, entry in enumerate(file_patterns_raw):
        if not isinstance(entry, dict):
            raise ValueError(f"rule pack {source}: file_patterns[{i}] must be a mapping")
        rule_id = entry.get("id")
        regex_str = entry.get("regex")
        kinds = entry.get("applies_to_kinds")
        if not isinstance(rule_id, str) or not rule_id:
            raise ValueError(f"rule pack {source}: file_patterns[{i}].id missing")
        if not isinstance(regex_str, str) or not regex_str:
            raise ValueError(f"rule pack {source}: file_patterns[{i}].regex missing")
        if not isinstance(kinds, list) or not kinds:
            raise ValueError(f"rule pack {source}: file_patterns[{i}].applies_to_kinds must be a non-empty list")
        try:
            pattern = re.compile(regex_str, re.IGNORECASE)
        except re.error as e:
            raise ValueError(f"rule pack {source}: file_patterns[{i}].regex is invalid: {e}") from e
        rules.append(
            FilePatternRule(
                id=rule_id,
                regex=pattern,
                applies_to_kinds=frozenset(str(k) for k in kinds),
            )
        )

    # Phase 6: rule-pack inheritance via `extends:`
    if isinstance(extends, str) and extends:
        base = _builtin_pack(extends)
        if base is None:
            raise ValueError(
                f"rule pack {source}: extends `{extends}` — no such built-in pack "
                f"(available: {sorted(_BUILTIN_PACKS.keys()) or ['default']})"
            )
        return _merge_packs(base, name, version, tuple(rules))

    return RulePack(name=name, version=version, file_patterns=tuple(rules))


def get_active_rules(rules_path: str | Path | None = None) -> RulePack:
    """Return the active RulePack — custom if path given, else default."""
    if rules_path is None:
        return RulePack.default()
    path = Path(rules_path) if not isinstance(rules_path, Path) else rules_path
    return load_rule_pack(path)
