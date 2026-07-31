"""W1440 — the contract that keeps law mining and law checking in one namespace.

Companion to ``test_w1439_laws_import_law_src_layout.py``. That file pins
the *symptom* (the import law fired backwards on a src-layout repo);
this one pins the *mechanism* that fixes it and the second half of the
same defect class — laws mined for symbol kinds no checker can observe.

Three properties are held here:

1. **One namespace.** The miner buckets file paths and the checker reads
   module paths. Both now route through :mod:`roam.laws.namespace`, which
   re-expresses a file path in the namespace an ``import`` statement can
   actually name. Source roots are *detected from repo layout*, never
   hardcoded, so this works for ``src/``, ``lib/``, ``python/`` or a
   monorepo's ``packages/app/src`` alike.
2. **Externality is asked of the repo, not of a list.** A hand-rolled
   stdlib allowlist is a false positive waiting for its module to be
   used — ``statistics`` was the one that came due.
3. **No law is mined that cannot be checked.** ``added_symbols()``
   recognises exactly ``function`` and ``class``, so a mined
   ``constant`` / ``method`` / ``property`` / ``variable`` law matched
   nothing on every diff and reported clean against a flagrant violation.

The end-to-end test mines and checks a repo that is *not* roam-code, so
what is verified is the src-layout convention itself rather than one
codebase's happenstance.
"""

from __future__ import annotations

import sqlite3
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import git_init, index_in_process  # noqa: E402

from roam.db.connection import open_db  # noqa: E402
from roam.laws.checker import (  # noqa: E402
    CHECKABLE_SYMBOL_KINDS,
    added_symbols,
    check_laws,
    parse_added,
)
from roam.laws.miner import Law, _mine_naming_laws, mine_laws  # noqa: E402
from roam.laws.namespace import (  # noqa: E402
    bucket_for_file,
    detect_source_roots,
    detect_source_roots_from_paths,
    namespace_contains,
)

# ---------------------------------------------------------------------------
# 1. Source-root detection
# ---------------------------------------------------------------------------


def test_src_is_detected_as_a_source_root_and_tests_is_not():
    """The discriminator is loose modules, not the directory's name.

    ``src/`` holds packages and nothing else, so files under it are named
    from its children down. ``tests/`` holds 1418 loose ``test_*.py``
    modules *alongside* a ``_helpers`` package — it is a package
    directory, and stripping it would rewrite ``tests/foo.py`` into a
    namespace nothing imports.
    """
    roots = detect_source_roots_from_paths(
        [
            "src/roam/__init__.py",
            "src/roam/db/__init__.py",
            "src/roam/db/connection.py",
            "tests/__init__.py",
            "tests/test_probe.py",
            "tests/_helpers/__init__.py",
            "tests/_helpers/repo_root.py",
        ]
    )

    assert roots == frozenset({"src"}), roots


def test_detection_is_layout_driven_not_a_hardcoded_src():
    """A source root named anything else is found by the same rule."""
    roots = detect_source_roots_from_paths(
        [
            "packages/app/python/appcore/__init__.py",
            "packages/app/python/appcore/main.py",
        ]
    )

    assert roots == frozenset({"packages/app/python"}), roots


def test_a_flat_layout_declares_no_source_root():
    """No root means no rewriting: repos that were never mis-bucketed
    must not move."""
    roots = detect_source_roots_from_paths(
        [
            "roam/__init__.py",
            "roam/db/__init__.py",
            "roam/db/connection.py",
            "setup.py",
        ]
    )

    assert roots == frozenset(), roots


def test_detection_reads_a_real_checkout_from_disk(tmp_path):
    """The checker has no index in CI — it reads the layout off disk."""
    (tmp_path / "src" / "widget").mkdir(parents=True)
    (tmp_path / "src" / "widget" / "__init__.py").write_text("")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_widget.py").write_text("import widget\n")

    assert detect_source_roots(tmp_path) == frozenset({"src"})


# ---------------------------------------------------------------------------
# 2. Bucketing lands in the import namespace
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "roots", "expected"),
    [
        # src layout: the bucket names what an import statement names.
        ("src/roam/db/connection.py", frozenset({"src"}), "roam"),
        # flat layout: byte-identical to the pre-fix behaviour.
        ("roam/db/connection.py", frozenset(), "roam/db"),
        ("tests/test_foo.py", frozenset({"src"}), "tests"),
        ("src/handlers/auth.py", frozenset(), "src/handlers"),
        ("app.py", frozenset({"src"}), ""),
        # a path that IS the source root names no importable namespace
        ("src/setup_helper.py", frozenset({"src"}), ""),
        # a leading dot is part of the directory name, not noise to strip:
        # ``lstrip("./")`` turned this into ``github/scripts``, a bucket
        # no diff path could ever match.
        (".github/scripts/gate.py", frozenset({"src"}), ".github/scripts"),
        ("./src/roam/db/connection.py", frozenset({"src"}), "roam"),
    ],
)
def test_bucket_for_file(path, roots, expected):
    assert bucket_for_file(path, roots) == expected


def test_a_dot_directory_law_matches_the_file_it_was_mined_from(tmp_path):
    """End of the round trip that the path mangling broke.

    The miner bucketed ``.github/scripts/gate.py`` as ``github/scripts``
    while the checker matched the real path, so the law was mined and
    then never fired — silently, on every repo with a dot-directory.
    """
    (tmp_path / ".github" / "scripts").mkdir(parents=True)
    (tmp_path / "vendor").mkdir()
    law = Law(
        id="imports_github_scripts_to_roam",
        kind="import",
        description="Files in .github/scripts/ import from roam/",
        evidence={"sample_size": 34, "conformance_pct": 79.4},
        confidence="low",
        rule={"kind": "import", "from_dir": ".github/scripts", "to_dir": "roam"},
    )

    violations = check_laws(
        [law],
        diff=_diff_for(".github/scripts/gate.py", "from vendor.shady import helper"),
        repo_root=tmp_path,
    )

    assert violations, "the .github/scripts law never governed .github/scripts"
    # ...and it must report the directory that exists, not the one the
    # character-strip invented.
    assert violations[0].evidence["from_dir"] == ".github/scripts"


def test_namespace_containment_is_segment_aligned():
    """The law names a directory; the import names a module inside it."""
    assert namespace_contains("roam", "roam/db/connection")
    assert namespace_contains("roam", "roam")
    # A prefix that is not a whole segment must not match.
    assert not namespace_contains("roam", "roamer/x")
    assert not namespace_contains("roam", "src/roam/db")


# ---------------------------------------------------------------------------
# 3. End-to-end on a src-layout repo that is not roam-code
# ---------------------------------------------------------------------------


@pytest.fixture
def src_layout_project(tmp_path, monkeypatch):
    """A conventional ``src/`` layout: package under ``src/``, tests beside it.

    Every test imports ``mypkg.db.conn`` — the spelling that resolves
    once ``pip install -e .`` puts ``src/`` on ``sys.path``, and the one
    a src-layout project actually writes.
    """
    proj = tmp_path / "srclayout"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")

    pkg = proj / "src" / "mypkg"
    (pkg / "db").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "db" / "__init__.py").write_text("")
    (pkg / "db" / "conn.py").write_text("def open_conn():\n    return 1\n")
    (pkg / "db" / "pool.py").write_text("def get_pool():\n    return []\n")

    tests_dir = proj / "tests"
    tests_dir.mkdir()
    for i in range(6):
        (tests_dir / f"test_case_{i}.py").write_text(
            f"from mypkg.db.conn import open_conn\n\n\ndef test_case_{i}():\n    assert open_conn()\n"
        )

    git_init(proj)
    monkeypatch.chdir(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed: {out}"
    return proj


def _diff_for(source_path: str, import_line: str) -> str:
    """A minimal new-file diff at *source_path* carrying one import."""
    return textwrap.dedent(
        f"""\
        diff --git a/{source_path} b/{source_path}
        new file mode 100644
        --- /dev/null
        +++ b/{source_path}
        @@ -0,0 +1,2 @@
        +{import_line}
        +
        """
    )


def _new_test_diff(import_line: str) -> str:
    return _diff_for("tests/test_new.py", import_line)


def test_mined_import_law_names_the_import_namespace(src_layout_project):
    """The law must say ``mypkg``, not ``src/mypkg``.

    ``src/mypkg`` is unsatisfiable: no import statement in a src-layout
    project can name it, so a law carrying it inverts the checker.
    """
    with open_db(readonly=True) as conn:
        laws = mine_laws(conn)

    import_laws = [law for law in laws if law.kind == "import"]
    assert import_laws, f"expected an import law, got: {[law.id for law in laws]}"
    targets = {law.rule.get("to_dir") for law in import_laws}
    assert "mypkg" in targets, targets
    assert "src/mypkg" not in targets, targets


def test_mined_law_clears_the_import_the_repo_actually_writes(src_layout_project):
    """Mine and check the same repo: the round trip must agree with itself."""
    with open_db(readonly=True) as conn:
        laws = mine_laws(conn)

    violations = check_laws(
        laws,
        diff=_new_test_diff("from mypkg.db.conn import open_conn"),
        repo_root=src_layout_project,
    )

    assert violations == [], [v.to_dict() for v in violations]


def test_mined_law_still_flags_an_import_that_leaves_the_namespace(src_layout_project):
    """Negative control for the two tests above.

    A fix that made the checker inert would satisfy them both. This one
    fails unless a genuine cross-namespace import is still reported.
    """
    (src_layout_project / "vendor").mkdir()
    (src_layout_project / "vendor" / "shady.py").write_text("def helper():\n    return 1\n")

    with open_db(readonly=True) as conn:
        laws = mine_laws(conn)

    violations = check_laws(
        laws,
        diff=_new_test_diff("from vendor.shady import helper"),
        repo_root=src_layout_project,
    )

    assert violations, "a real cross-namespace import was not reported"
    assert violations[0].kind == "import"
    assert "vendor" in violations[0].message


# ---------------------------------------------------------------------------
# 4. Externality is asked of the repo, not of a hand-rolled list
# ---------------------------------------------------------------------------


def _tests_to_pkg_law() -> Law:
    return Law(
        id="imports_tests_to_roam",
        kind="import",
        description="Files in tests/ import from roam/",
        evidence={"sample_size": 6265, "conformance_pct": 81.5},
        confidence="medium",
        rule={"kind": "import", "from_dir": "tests", "to_dir": "roam"},
    )


@pytest.mark.parametrize("module", ["statistics", "zoneinfo", "graphlib", "dataclasses"])
def test_stdlib_import_is_external_even_when_the_repo_shadows_the_name(module, tmp_path):
    """``statistics`` is the one that came due — it was missing from the
    hand-rolled allowlist and produced a measured false positive in this
    repo's own history. The list is now ``sys.stdlib_module_names``, so
    there is no next one: ``zoneinfo`` and ``graphlib`` post-date the
    allowlist it replaced and are covered without anyone noticing them.

    A same-named directory in the repo is planted deliberately — it is
    what isolates this from the repo-ownership probe, which would
    otherwise answer "external" for its own reasons.
    """
    (tmp_path / "tests").mkdir()
    (tmp_path / module).mkdir()

    violations = check_laws(
        [_tests_to_pkg_law()],
        diff=_new_test_diff(f"import {module}"),
        repo_root=tmp_path,
    )

    assert violations == [], [v.to_dict() for v in violations]


def test_a_module_the_repo_does_not_contain_is_external(tmp_path):
    """Third-party imports are nobody's layering violation, and the
    checker settles that by looking at the checkout rather than by
    recognising the name."""
    (tmp_path / "tests").mkdir()

    violations = check_laws(
        [_tests_to_pkg_law()],
        diff=_new_test_diff("import some_library_nobody_listed"),
        repo_root=tmp_path,
    )

    assert violations == [], [v.to_dict() for v in violations]


def test_a_directory_the_repo_does_contain_is_internal(tmp_path):
    """Negative control for the test above: same shape, one difference —
    the repo owns this one."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "some_library_nobody_listed").mkdir()

    violations = check_laws(
        [_tests_to_pkg_law()],
        diff=_new_test_diff("import some_library_nobody_listed"),
        repo_root=tmp_path,
    )

    assert violations, "an import of the repo's own directory was treated as third-party"


@pytest.mark.parametrize(
    ("source_path", "import_line"),
    [
        ("tests/test_new.py", "from . import helpers"),
        ("tests/test_new.py", "from .helpers import thing"),
        ("tests/sub/test_deep.py", "from ..helpers import thing"),
        ("tests/test_new.py", "import x from './helpers'"),
        ("tests/sub/test_deep.py", "const x = require('../cousin/mod')"),
    ],
)
def test_relative_imports_are_resolved_not_treated_as_top_level_packages(source_path, import_line, tmp_path):
    """``from .helpers import x`` names a sibling, not a package
    ``helpers``. Resolving it against the importing file's own directory
    is the same namespace reconciliation as the src-root strip — without
    it, a relative import reads as an import of whatever top-level
    package happens to share its first segment.

    The repo is given colliding ``helpers/`` and ``cousin/`` directories
    on purpose: the mis-parse is only *visible* when the name it invents
    is one the repo owns, which is also the case where the resulting
    violation is most convincing and most wrong.
    """
    (tmp_path / "tests" / "sub").mkdir(parents=True)
    (tmp_path / "helpers").mkdir()
    (tmp_path / "cousin").mkdir()

    violations = check_laws(
        [_tests_to_pkg_law()],
        diff=_diff_for(source_path, import_line),
        repo_root=tmp_path,
    )

    assert violations == [], [v.to_dict() for v in violations]


# ---------------------------------------------------------------------------
# 5. No law is mined that cannot be checked
# ---------------------------------------------------------------------------


def test_every_checkable_kind_is_actually_observable_in_a_diff():
    """Guards the dangerous direction of the drift.

    Widening ``CHECKABLE_SYMBOL_KINDS`` without teaching
    ``added_symbols`` to emit the kind re-creates exactly the defect this
    file exists to prevent: a mined law that matches nothing, forever.
    """
    diff = textwrap.dedent(
        """\
        diff --git a/src/pkg/mod.py b/src/pkg/mod.py
        new file mode 100644
        --- /dev/null
        +++ b/src/pkg/mod.py
        @@ -0,0 +1,4 @@
        +def a_function(x):
        +    return x
        +class AClass:
        +    pass
        """
    )

    observed = {sym["kind"] for sym in added_symbols(parse_added(diff))}

    assert CHECKABLE_SYMBOL_KINDS <= observed, (
        f"claimed checkable but never emitted: {CHECKABLE_SYMBOL_KINDS - observed}"
    )


def test_a_law_over_an_unobservable_kind_could_never_have_fired():
    """The evidence for not mining these: a flagrant violation reports clean.

    This is not an assertion that the checker *should* stay blind — it
    is the measurement that makes mining such a law indefensible.
    """
    diff = textwrap.dedent(
        """\
        diff --git a/src/pkg/mod.py b/src/pkg/mod.py
        new file mode 100644
        --- /dev/null
        +++ b/src/pkg/mod.py
        @@ -0,0 +1,3 @@
        +badlyNamedConstant = 1
        +anotherOffender = 2
        """
    )
    laws = [
        Law(
            id=f"{style}_{kind}s",
            kind="naming",
            description=f"{kind}s must be {style}",
            evidence={"sample_size": 701, "conformance_pct": 100},
            confidence="high",
            rule={"kind": "naming", "symbol_kind": kind, "style": style},
        )
        for kind, style in (
            ("constant", "UPPER_SNAKE"),
            ("method", "snake_case"),
            ("property", "snake_case"),
            ("variable", "snake_case"),
        )
    ]

    assert check_laws(laws, diff=diff) == []


def test_naming_laws_are_not_mined_for_unobservable_kinds(monkeypatch):
    """All six kinds have a dominant style; only the checkable two ship."""
    import roam.commands.conventions_helper as helper

    def fake_conventions(_conn, **_kwargs):
        return {
            "by_kind": {
                kind: {"total": 500, "pct": 100.0, "style": style, "breakdown": {}}
                for kind, style in (
                    ("function", "snake_case"),
                    ("class", "PascalCase"),
                    ("constant", "UPPER_SNAKE"),
                    ("method", "snake_case"),
                    ("property", "snake_case"),
                    ("variable", "snake_case"),
                )
            },
            "outliers": [],
        }

    monkeypatch.setattr(helper, "compute_conventions", fake_conventions)
    conn = sqlite3.connect(":memory:")  # no symbols table -> no examples, by design
    conn.row_factory = sqlite3.Row
    diagnostics: dict = {}

    laws = _mine_naming_laws(conn, 70.0, 5, diagnostics)

    assert {law.rule["symbol_kind"] for law in laws} == {"function", "class"}
    assert diagnostics["skipped_naming_kinds"] == ["constant", "method", "property", "variable"]
