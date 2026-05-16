"""AST-literal contract for `cli._SARIF_CONSUMERS`.

W713 drive-by on W702: pin the constant to a literal tuple of string
constants so a future edit cannot silently make it dynamic (e.g. computed
from a runtime scan, mutated by an import-time side effect, or annotated
without an inline value). A dynamic constant defeats the W22.3 drift
guard in `tests/test_sarif_consumer_list.py` — that test imports the
runtime value and would happily compare against a wrong tuple.

# W714 follow-up: extract helper into tests/_helpers/cli_ast.py once a
# second AST-literal contract test lands.
"""

from __future__ import annotations

import ast
from pathlib import Path

from roam import cli as cli_mod

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CLI_PATH = _REPO_ROOT / "src" / "roam" / "cli.py"


def _find_sarif_consumers_node() -> ast.AST:
    tree = ast.parse(_CLI_PATH.read_text(encoding="utf-8"), filename=str(_CLI_PATH))
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "_SARIF_CONSUMERS":
                assert node.value is not None, (
                    "_SARIF_CONSUMERS is annotated without an inline value; "
                    "the constant must be a literal tuple assignment."
                )
                return node.value
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_SARIF_CONSUMERS":
                    return node.value
    raise AssertionError("_SARIF_CONSUMERS not found as module-level assignment in src/roam/cli.py")


def test_sarif_consumers_is_literal_tuple_of_strings() -> None:
    value = _find_sarif_consumers_node()
    assert isinstance(value, ast.Tuple), (
        f"_SARIF_CONSUMERS must be a literal tuple (ast.Tuple); "
        f"got {type(value).__name__}. Dynamic construction defeats the "
        f"W22.3 drift guard in tests/test_sarif_consumer_list.py."
    )
    non_string = [elt for elt in value.elts if not (isinstance(elt, ast.Constant) and isinstance(elt.value, str))]
    assert not non_string, f"_SARIF_CONSUMERS contains non-string-literal elements: {[ast.dump(e) for e in non_string]}"


def test_sarif_consumers_ast_matches_runtime() -> None:
    value = _find_sarif_consumers_node()
    assert isinstance(value, ast.Tuple)
    parsed = tuple(elt.value for elt in value.elts)  # type: ignore[attr-defined]
    assert parsed == cli_mod._SARIF_CONSUMERS, (
        f"AST-parsed _SARIF_CONSUMERS diverges from runtime value.\n"
        f"  parsed:  {parsed}\n"
        f"  runtime: {cli_mod._SARIF_CONSUMERS}"
    )
