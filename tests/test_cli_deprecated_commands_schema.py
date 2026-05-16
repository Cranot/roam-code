"""AST-literal schema test for ``cli._DEPRECATED_COMMANDS``.

W757 / W702 drive-by: ``test_sarif_consumer_list.py`` already asserts
that every entry in ``_DEPRECATED_COMMANDS`` is a real registered alias,
but nothing pins the *literal shape* of the constant itself. If a
contributor refactors the dict-of-dicts into a dataclass call, a
helper-built dict comprehension, or any non-literal RHS, the help-text
formatter and the JSON-envelope deprecation-warning builder would
silently break because both walk ``_DEPRECATED_COMMANDS`` at import
time, before the helper has a chance to run.

This test parses ``src/roam/cli.py`` with ``ast`` and asserts:

1. ``_DEPRECATED_COMMANDS`` appears exactly once at module level as a
   plain ``ast.Assign`` (or ``ast.AnnAssign``) whose RHS is an
   ``ast.Dict`` literal -- no function calls, comprehensions, merges,
   or starred unpacks.
2. Every key in that dict literal is an ``ast.Constant`` string.
3. Every value is itself an ``ast.Dict`` literal whose keys are
   ``ast.Constant`` strings drawn from a closed enumeration
   (``replacement`` / ``reason`` / ``removal_version``) and whose
   values are ``ast.Constant`` strings.
4. The parsed literal evaluates byte-for-byte equal to the runtime
   imported ``cli._DEPRECATED_COMMANDS`` dict.

Per CLAUDE.md Constraint 8 (closed enumeration > free string
composition) and the agentic-assurance discipline that every closed
enumeration ships with a drift guard.
"""

from __future__ import annotations

import ast
from pathlib import Path

from roam.cli import _DEPRECATED_COMMANDS

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CLI_PATH = _REPO_ROOT / "src" / "roam" / "cli.py"

# Closed enumeration of allowed inner-dict keys. Matches the schema
# documented in src/roam/cli.py:21-24 and consumed by
# `_format_deprecation_notice` / `_deprecation_record`.
_ALLOWED_INNER_KEYS: frozenset[str] = frozenset(
    {"replacement", "reason", "removal_version"},
)


def _find_deprecated_commands_assign(tree: ast.Module) -> ast.Dict:
    """Return the ``ast.Dict`` literal RHS of the
    ``_DEPRECATED_COMMANDS`` module-level assignment.

    Fails the test (via ``AssertionError``) if the constant is missing,
    appears more than once, is not assigned at module level, or has a
    non-literal RHS.
    """
    matches: list[ast.Dict] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_DEPRECATED_COMMANDS":
                    assert isinstance(node.value, ast.Dict), (
                        "_DEPRECATED_COMMANDS RHS must be a dict literal, "
                        f"got {type(node.value).__name__} at line {node.lineno}. "
                        "Helper-built dicts break help-text formatters that walk "
                        "the constant at import time."
                    )
                    matches.append(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "_DEPRECATED_COMMANDS":
                assert isinstance(node.value, ast.Dict), (
                    "_DEPRECATED_COMMANDS RHS must be a dict literal, "
                    f"got {type(node.value).__name__} at line {node.lineno}."
                )
                matches.append(node.value)
    assert len(matches) == 1, (
        f"Expected exactly one module-level _DEPRECATED_COMMANDS assignment in src/roam/cli.py; found {len(matches)}."
    )
    return matches[0]


def _eval_literal_dict(dict_node: ast.Dict) -> dict[str, dict[str, str]]:
    """Evaluate the dict literal via ``ast.literal_eval`` and return it.

    ``literal_eval`` raises ``ValueError`` on any non-literal node, which
    surfaces as a test failure rather than silently swallowing a
    function-call or starred-unpack RHS.
    """
    return ast.literal_eval(dict_node)


def test_deprecated_commands_is_module_level_dict_literal() -> None:
    """The constant must be a single module-level ``ast.Dict`` literal.

    Catches refactors that swap the literal for a helper call, dict
    comprehension, or runtime merge -- any of which would defeat
    import-time walkers in the help-text formatter.
    """
    source = _CLI_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(_CLI_PATH))
    _find_deprecated_commands_assign(tree)


def test_deprecated_commands_literal_keys_are_constant_strings() -> None:
    """Every outer key must be an ``ast.Constant`` of type ``str``.

    Forbids star-unpack (``**other_dict``), computed keys, or non-string
    keys -- all of which would silently bypass the help-text scan.
    """
    source = _CLI_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(_CLI_PATH))
    dict_node = _find_deprecated_commands_assign(tree)

    bad_keys: list[str] = []
    for key_node in dict_node.keys:
        if key_node is None:
            bad_keys.append("<starred unpack: **other>")
            continue
        if not isinstance(key_node, ast.Constant) or not isinstance(key_node.value, str):
            bad_keys.append(ast.unparse(key_node))
    assert not bad_keys, f"_DEPRECATED_COMMANDS outer keys must all be string literals; non-literal entries: {bad_keys}"


def test_deprecated_commands_literal_values_are_inner_dict_literals() -> None:
    """Every outer value must be an ``ast.Dict`` of constant-string
    key/value pairs drawn from the closed enumeration.
    """
    source = _CLI_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(_CLI_PATH))
    dict_node = _find_deprecated_commands_assign(tree)

    violations: list[str] = []
    for key_node, value_node in zip(dict_node.keys, dict_node.values):
        alias = key_node.value if isinstance(key_node, ast.Constant) else "<unknown>"
        if not isinstance(value_node, ast.Dict):
            violations.append(
                f"{alias!r}: value is {type(value_node).__name__}, expected Dict literal",
            )
            continue
        for inner_key, inner_val in zip(value_node.keys, value_node.values):
            if inner_key is None:
                violations.append(f"{alias!r}: inner dict has starred unpack")
                continue
            if not isinstance(inner_key, ast.Constant) or not isinstance(inner_key.value, str):
                violations.append(f"{alias!r}: inner key is not a string literal")
                continue
            if inner_key.value not in _ALLOWED_INNER_KEYS:
                violations.append(
                    f"{alias!r}: inner key {inner_key.value!r} not in closed enumeration {sorted(_ALLOWED_INNER_KEYS)}",
                )
            if not isinstance(inner_val, ast.Constant) or not isinstance(inner_val.value, str):
                violations.append(
                    f"{alias!r}: inner value for {inner_key.value!r} is not a string literal",
                )
    assert not violations, (
        "_DEPRECATED_COMMANDS inner shape drift -- expected "
        "`dict[str, dict[str, str]]` literals with keys in "
        f"{sorted(_ALLOWED_INNER_KEYS)}:\n" + "\n".join(f"  - {v}" for v in violations)
    )


def test_deprecated_commands_parsed_literal_equals_runtime_value() -> None:
    """``ast.literal_eval`` of the source RHS must equal the runtime dict.

    The two paths diverging would signal an import-time mutation
    (e.g. monkey-patch in a sibling module), which would defeat the
    "the literal IS the contract" guarantee this test exists to enforce.
    """
    source = _CLI_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(_CLI_PATH))
    dict_node = _find_deprecated_commands_assign(tree)
    parsed_literal = _eval_literal_dict(dict_node)
    assert parsed_literal == _DEPRECATED_COMMANDS, (
        "Parsed AST literal diverges from runtime _DEPRECATED_COMMANDS. "
        "Something is mutating the dict at import time -- the literal "
        "must remain the single source of truth.\n"
        f"  parsed:  {parsed_literal}\n"
        f"  runtime: {_DEPRECATED_COMMANDS}"
    )


def test_deprecated_commands_inner_replacement_targets_resolve() -> None:
    """Every ``replacement`` value must name a real, non-deprecated CLI
    command. Catches typos like ``"weather"`` -> ``"wether"`` that would
    route a deprecated alias into a phantom command.
    """
    # Import here so the AST tests above stay independent of the CLI
    # surface; the lazy-loading group makes this fast.
    from roam.cli import _COMMANDS

    bad: list[str] = []
    for alias, record in _DEPRECATED_COMMANDS.items():
        replacement = record.get("replacement")
        if replacement is None:
            bad.append(f"{alias!r}: no 'replacement' key")
            continue
        if replacement not in _COMMANDS:
            bad.append(f"{alias!r}: replacement {replacement!r} is not in _COMMANDS")
            continue
        if replacement in _DEPRECATED_COMMANDS:
            bad.append(
                f"{alias!r}: replacement {replacement!r} is itself deprecated (would chain deprecation warnings)",
            )
    assert not bad, "\n".join(bad)
