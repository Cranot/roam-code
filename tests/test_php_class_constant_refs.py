"""PHP ``X::class`` constant-expression reference extraction.

Measured false-positive this pins: roam's PHP extractor emitted NO
reference edge for the ``::class`` constant expression (tree-sitter-php
node type ``class_constant_access_expression``). A class referenced only
via ``X::class`` — Laravel ``$casts = ['col' => MyCast::class]``,
``$dispatchesEvents``, provider ``$bindings``, config arrays — therefore
produced no *incoming* edge, and ``roam dead`` falsely labelled it
unreferenced / safe to delete.

``PhpExtractor._extract_class_constant_ref`` now emits a reference to the
left-hand class for the ``::class`` form only, mirroring the class-
reference emission ``_extract_new`` already uses for ``new X()``
(``kind="call"``, ``line``, ``source_name``).

Two test layers:

* **Unit** — parse real PHP with the tree-sitter grammar and call
  ``extract_references`` directly. Proves the parser change fires ONLY on
  ``::class`` (not ``X::SOME_CONST``, not ``self/static/parent::class``,
  not dynamic ``$obj::class``) and never explodes edge counts on normal
  static calls / constant-heavy code.
* **End-to-end** — index a tiny PHP project and assert on the ``edges``
  table (the literal FP mechanism: "no inbound edge") plus the
  user-facing ``roam dead`` output. ``tn``: a class referenced ONLY via
  ``['col' => Foo::class]`` gains an inbound edge and drops out of the
  dead set. ``tp``: a genuinely-unreferenced class stays dead.
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from roam.cli import cli
from roam.db.connection import open_db
from roam.languages.php_lang import PhpExtractor
from tests.conftest import make_src_project

# ---------------------------------------------------------------------------
# Unit — direct extract_references() on the real tree-sitter parse
# ---------------------------------------------------------------------------


def _refs(php_source: str) -> list[dict]:
    """Parse ``php_source`` and return the extractor's reference dicts."""
    from tree_sitter_language_pack import get_parser

    parser = get_parser("php")
    src = php_source.encode("utf-8")
    tree = parser.parse(src)
    return PhpExtractor().extract_references(tree, src, "x.php")


def _targets(refs: list[dict]) -> list[str]:
    return [r["target_name"] for r in refs]


class TestClassConstantReferenceEmission:
    def test_bare_class_constant_produces_reference(self):
        """``Foo::class`` emits a ``call`` reference to ``Foo``."""
        refs = _refs("<?php $x = Foo::class;")
        matches = [r for r in refs if r["target_name"] == "Foo"]
        assert len(matches) == 1
        assert matches[0]["kind"] == "call"

    def test_casts_array_value_produces_reference(self):
        """The canonical Laravel FP: ``['col' => MyCast::class]`` references MyCast."""
        refs = _refs("<?php class W { protected $casts = ['col' => MyCast::class]; }")
        assert "MyCast" in _targets(refs)

    def test_qualified_left_hand_side_uses_simple_name(self):
        """``\\App\\Casts\\MyCast::class`` resolves to the simple name ``MyCast``
        (mirrors how ``_extract_new`` shortens qualified class names)."""
        refs = _refs("<?php class W { protected $casts = ['col' => \\App\\Casts\\MyCast::class]; }")
        assert "MyCast" in _targets(refs)
        # The fully-qualified string must NOT leak through as the target.
        assert "\\App\\Casts\\MyCast" not in _targets(refs)

    def test_dispatches_events_array_references_each_class(self):
        """``$dispatchesEvents`` maps to several ``::class`` values — each
        must produce its own reference."""
        refs = _refs(
            "<?php class M { protected $dispatchesEvents = ['saved' => Saved::class, 'deleted' => Deleted::class]; }"
        )
        targets = _targets(refs)
        assert "Saved" in targets
        assert "Deleted" in targets

    def test_self_static_parent_class_are_skipped(self):
        """``self::class`` / ``static::class`` / ``parent::class`` reference no
        external class (relative_scope), matching how ``_extract_scoped_call``
        skips self/static/parent."""
        for scope in ("self", "static", "parent"):
            refs = _refs(f"<?php class W {{ function f() {{ return {scope}::class; }} }}")
            assert refs == [], f"{scope}::class should emit no reference, got {refs}"

    def test_other_class_constant_does_not_reference_class(self):
        """Conservative firing: ``Foo::SOME_CONST`` (a normal class constant,
        not the magic ``::class``) must NOT emit a reference to ``Foo``."""
        refs = _refs("<?php $x = Foo::SOME_CONST;")
        assert "Foo" not in _targets(refs)
        assert refs == []

    def test_dynamic_scope_class_is_skipped(self):
        """``$obj::class`` has a dynamic (non-static-name) left-hand side and
        cannot resolve to a class name — no reference is emitted."""
        refs = _refs("<?php function f($obj) { return $obj::class; }")
        assert refs == []

    def test_normal_static_call_unchanged(self):
        """A regular ``Foo::bar()`` still yields its ``Foo.bar`` scoped-call
        reference and NO spurious bare ``Foo`` ``::class``-style reference."""
        refs = _refs("<?php Foo::bar();")
        targets = _targets(refs)
        assert "Foo.bar" in targets
        assert "Foo" not in targets  # the ::class handler must not fire here

    def test_no_edge_explosion_on_constant_heavy_code(self):
        """Constant-heavy code must not gain references from every
        ``X::CONST`` access — only genuine ``::class``, calls, and ``new``
        emit refs. Guards the high-blast-radius over-firing risk."""
        refs = _refs("<?php $a = A::X; $b = B::Y; $c = C::Z; Foo::bar(); new Baz();")
        targets = _targets(refs)
        # Only the real call + constructor produce refs; the three bare
        # class-constant accesses (A::X, B::Y, C::Z) produce none.
        assert "Foo.bar" in targets
        assert "Baz" in targets
        assert "A" not in targets
        assert "B" not in targets
        assert "C" not in targets


# ---------------------------------------------------------------------------
# End-to-end — index a PHP project; assert on edges + roam dead
# ---------------------------------------------------------------------------

# ``MyCast`` is referenced ONLY via a fully-qualified ``::class`` in a
# ``$casts`` array — deliberately WITHOUT a ``use`` import, so the ``::class``
# reference is the ONLY thing that can create an inbound edge to it. ``Ghost``
# is referenced nowhere.
_MYCAST_PHP = """
<?php
namespace App\\Casts;
class MyCast {
    public function get() { return 1; }
}
"""

_WIDGET_PHP = """
<?php
namespace App\\Models;
class Widget {
    protected $casts = ['col' => \\App\\Casts\\MyCast::class];
}
"""

_GHOST_PHP = """
<?php
namespace App\\Ghost;
class Ghost {
    public function unused() { return 2; }
}
"""


def _index_php_project(tmp_path):
    """Build + index a 3-class PHP project; return the project root."""
    proj = make_src_project(
        tmp_path,
        {
            "Casts/MyCast.php": _MYCAST_PHP,
            "Models/Widget.php": _WIDGET_PHP,
            "Ghost/Ghost.php": _GHOST_PHP,
        },
    )
    runner = CliRunner()
    old_cwd = __import__("os").getcwd()
    __import__("os").chdir(str(proj))
    try:
        result = runner.invoke(cli, ["index"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
    finally:
        __import__("os").chdir(old_cwd)
    return proj


def _class_id(conn, name: str) -> int | None:
    row = conn.execute("SELECT id FROM symbols WHERE name = ? AND kind = 'class'", (name,)).fetchone()
    return row["id"] if row else None


def _dead_class_names(runner) -> set[str]:
    """Names appearing in the dead-export findings (SAFE + REVIEW buckets)."""
    result = runner.invoke(cli, ["dead", "--json", "--detail"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    names: set[str] = set()
    for bucket in ("high_confidence", "low_confidence"):
        for finding in data.get(bucket) or []:
            value = finding.get("value", finding) if isinstance(finding, dict) else {}
            for key in ("name", "qualified_name"):
                if value.get(key):
                    names.add(value[key])
    return names


class TestClassConstOnlyReferenceIsNotDead:
    def test_class_referenced_only_via_class_const_gains_inbound_edge(self, tmp_path):
        """tn — ``MyCast``, referenced ONLY via ``['col' => MyCast::class]``,
        now has an inbound edge (sourced from the ``Widget`` class where the
        ``::class`` appears). Pre-fix it had zero inbound edges and was a FP."""
        import os

        proj = _index_php_project(tmp_path)
        old_cwd = os.getcwd()
        os.chdir(str(proj))
        try:
            with open_db(readonly=True) as conn:
                mycast_id = _class_id(conn, "MyCast")
                widget_id = _class_id(conn, "Widget")
                assert mycast_id is not None, "MyCast class was not indexed"
                inbound = conn.execute(
                    "SELECT source_id FROM edges WHERE target_id = ?",
                    (mycast_id,),
                ).fetchall()
                assert len(inbound) >= 1, "MyCast still has no inbound edge (the ::class FP)"
                # Provenance: the edge originates at the ``Widget`` class — the
                # site of the ``::class`` reference — not from nowhere.
                assert widget_id in {r["source_id"] for r in inbound}

            runner = CliRunner()
            dead_names = _dead_class_names(runner)
            assert "MyCast" not in dead_names, "MyCast is still flagged dead despite the ::class reference"
        finally:
            os.chdir(old_cwd)


class TestGenuinelyUnreferencedClassStillDead:
    def test_unreferenced_class_has_no_inbound_edge_and_is_dead(self, tmp_path):
        """tp — ``Ghost`` is referenced nowhere: it must still have zero
        inbound edges and still be reported as a dead export. Guards against
        the ``::class`` handler over-firing."""
        import os

        proj = _index_php_project(tmp_path)
        old_cwd = os.getcwd()
        os.chdir(str(proj))
        try:
            with open_db(readonly=True) as conn:
                ghost_id = _class_id(conn, "Ghost")
                assert ghost_id is not None, "Ghost class was not indexed"
                inbound = conn.execute(
                    "SELECT COUNT(*) FROM edges WHERE target_id = ?",
                    (ghost_id,),
                ).fetchone()[0]
                assert inbound == 0, "genuinely-unreferenced Ghost gained a spurious inbound edge"

            runner = CliRunner()
            dead_names = _dead_class_names(runner)
            assert "Ghost" in dead_names, "genuinely-unreferenced Ghost is no longer detected as dead"
        finally:
            os.chdir(old_cwd)
