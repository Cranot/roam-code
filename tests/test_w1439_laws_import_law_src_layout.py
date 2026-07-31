"""W1439 — the ``import``-kind law is 100% false-positive on src-layout repos.

Root cause: the miner and the checker bucket into two different
namespaces that can never intersect for a ``src/`` layout project.

* :func:`roam.laws.miner._import_bucket` buckets **file paths** taken
  from the index, so ``src/roam/db/connection.py`` becomes ``src/roam``
  and the mined law reads ``tests -> src/roam``.
* :func:`roam.laws.checker._resolve_import_target` turns an import
  statement into a **module path**, so ``from roam.db.connection import
  open_db`` becomes ``roam/db/connection`` and
  :func:`~roam.laws.checker._path_bucket` reduces it to ``roam/db``.

``roam/db`` is never equal to (nor under) ``src/roam``, so every
conventional internal import is reported as a layering violation while
the one spelling that would satisfy the comparison — ``from
src.roam...`` — does not resolve at runtime under a src layout.

Measured on this repo at W1439: mining a fresh index produced 11 laws;
running ``roam laws check`` over the last 29 non-merge commits raised 45
violations, of which 45 came from ``import`` laws and 0 from ``naming``
laws. Spot-checking every one of the 45 found no true positive — they
were conventional ``from roam.<pkg> import <name>`` lines, the repo's own
mandated ``from tests._helpers.repo_root import repo_root`` helper, and
one stdlib module (``statistics``) absent from the checker's hand-rolled
allowlist.

The pre-existing coverage in ``test_laws_mining.py::test_check_import_violation``
passes only because its fixture diff imports ``from src.forbidden.module
import bad_helper`` — a module path that includes the source root, which
a real src-layout project never writes.

FIXED: both halves now bucket through :mod:`roam.laws.namespace`, which
re-expresses file paths in the import namespace (``src/roam`` -> ``roam``)
using source roots detected from repo layout. The two tests below shipped
as ``xfail(strict=True)`` pins; they flipped to XPASS the moment the
namespaces were reconciled and are now ordinary assertions. Same repo,
same 29 commits, after the fix: 47 raised -> 1 raised.
"""

from __future__ import annotations

import textwrap

from roam.laws.checker import check_laws
from roam.laws.miner import Law


def _tests_to_src_roam_law() -> Law:
    """The exact law ``roam laws mine`` emits for this repo.

    Mined values at W1439: ``sample_size=6371``, ``conformance_pct=80.2``,
    ``confidence=medium`` — the highest-sample import law of the five.
    """
    return Law(
        id="imports_tests_to_src_roam",
        kind="import",
        description="Files in tests/ import from src/roam/",
        evidence={"sample_size": 6371, "conformance_pct": 80.2},
        severity="advisory",
        confidence="medium",
        rule={"kind": "import", "from_dir": "tests", "to_dir": "src/roam"},
    )


def _new_test_file_diff(import_line: str) -> str:
    """A minimal new-file diff under ``tests/`` carrying one import."""
    return textwrap.dedent(
        f"""\
        diff --git a/tests/test_probe.py b/tests/test_probe.py
        new file mode 100644
        --- /dev/null
        +++ b/tests/test_probe.py
        @@ -0,0 +1,2 @@
        +{import_line}
        +
        """
    )


def test_naming_law_still_fires_so_the_checker_is_not_wholly_inert():
    """Positive control: the naming checker does raise on a real diff.

    Without this, the two xfails below would also be satisfied by a
    checker that silently returns ``[]`` for everything, and the pins
    would prove nothing.
    """
    law = Law(
        id="snake_case_functions",
        kind="naming",
        description="Functions must be snake_case",
        evidence={"sample_size": 1729, "conformance_pct": 100},
        severity="advisory",
        confidence="high",
        rule={"kind": "naming", "symbol_kind": "function", "style": "snake_case"},
    )
    diff = textwrap.dedent(
        """\
        diff --git a/src/roam/probe_mod.py b/src/roam/probe_mod.py
        new file mode 100644
        --- /dev/null
        +++ b/src/roam/probe_mod.py
        @@ -0,0 +1,2 @@
        +def camelCaseOffender(x):
        +    return x
        """
    )

    violations = check_laws([law], diff=diff)

    assert len(violations) == 1, [v.to_dict() for v in violations]
    assert violations[0].law_id == "snake_case_functions"
    assert "camelCase" in violations[0].message


def test_conventional_src_layout_import_is_not_a_violation():
    """``from roam.db.connection import open_db`` is how every test here imports.

    It is the correct spelling under this project's src layout and must
    not be reported as an import-layering violation.
    """
    violations = check_laws(
        [_tests_to_src_roam_law()],
        diff=_new_test_file_diff("from roam.db.connection import open_db"),
    )

    assert violations == [], [v.to_dict() for v in violations]


def test_src_prefixed_import_that_cannot_resolve_is_reported():
    """``from src.roam...`` is unimportable under a src layout.

    The package installed on ``sys.path`` is ``roam``; there is no ``src``
    package. Whatever the layering verdict, the checker must not treat
    this spelling as *more* conforming than the one that actually works.
    """
    violations = check_laws(
        [_tests_to_src_roam_law()],
        diff=_new_test_file_diff("from src.roam.db.connection import open_db"),
    )

    assert violations != [], "the unresolvable src-prefixed import was cleared"
