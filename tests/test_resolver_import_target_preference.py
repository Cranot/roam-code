"""W181: resolver-level import-target preference.

Upstream fix for the phantom ``kind='import'`` edge class first surfaced
in W158 (laws-miner workaround: drop non-test -> test edges) and W167
(defensive text-check: drop edges whose target name isn't in the source's
actual import statements). W181 catches the bug at the *resolver* layer:
when ``import X`` reaches :func:`resolve_references`, the candidate set
for ``X`` is pre-filtered to drop kinds that are never legitimate import
targets (``local``, ``parameter``, ``property``) and ``variable``
candidates that live in test files. If the filter empties the set, the
resolver emits NO edge (strictly better than a phantom edge to a
same-named local in a test file).

The W167 text-check and W158 laws-miner filter are kept as
defence-in-depth, but for the import-edge bug class the W181 layer is
now the primary fix.
"""

from __future__ import annotations

from pathlib import Path

from roam.index import relations as relations_mod
from roam.index.relations import resolve_references


# ---------------------------------------------------------------------------
# helpers (lightly adapted from tests/test_index_import_verification.py so
# the two test files stay independent — duplicating ~20 lines is cheaper
# than coupling the suites).
# ---------------------------------------------------------------------------
def _build_project(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        full = tmp_path / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(body, encoding="utf-8")
    return tmp_path


def _sym(
    sym_id: int,
    name: str,
    *,
    file_path: str,
    qn: str | None = None,
    kind: str = "function",
    line_start: int = 1,
    line_end: int = 1,
) -> dict:
    return {
        "id": sym_id,
        "name": name,
        "qualified_name": qn or name,
        "kind": kind,
        "file_path": file_path,
        "line_start": line_start,
        "line_end": line_end,
    }


def _build_inputs(symbols: list[dict]) -> tuple[dict[str, list[dict]], dict[str, int]]:
    by_name: dict[str, list[dict]] = {}
    for s in symbols:
        by_name.setdefault(s["name"], []).append(s)
    files_by_path: dict[str, int] = {}
    next_fid = 1
    for s in symbols:
        if s["file_path"] not in files_by_path:
            files_by_path[s["file_path"]] = next_fid
            next_fid += 1
    return by_name, files_by_path


# ---------------------------------------------------------------------------
# W181 resolver tests
# ---------------------------------------------------------------------------
class TestImportTargetPreference:
    def test_import_resolves_to_module_not_local(self, tmp_path):
        """Two symbols share the name ``yaml``: one is a ``module`` kind
        in ``vendor/yaml.py``, one is a ``variable`` kind in
        ``tests/test_x.py``. The resolver MUST pick the module."""
        project = _build_project(
            tmp_path,
            {"src/foo.py": "import yaml\n\ndef use_yaml():\n    return yaml\n"},
        )
        symbols = [
            _sym(1, "use_yaml", file_path="src/foo.py", line_start=3, line_end=4),
            _sym(2, "yaml", file_path="vendor/yaml.py", kind="module"),
            _sym(3, "yaml", file_path="tests/test_x.py", kind="variable"),
        ]
        symbols_by_name, files_by_path = _build_inputs(symbols)
        refs = [
            {
                "source_name": "use_yaml",
                "target_name": "yaml",
                "kind": "import",
                "line": 1,
                "source_file": "src/foo.py",
                "import_path": "yaml",
            },
        ]
        edges = resolve_references(
            refs, symbols_by_name, files_by_path,
            project_root=str(project),
        )
        import_edges = [e for e in edges if e["kind"] == "import"]
        assert len(import_edges) == 1, edges
        # The target must be the module symbol (id=2), not the variable (id=3).
        assert import_edges[0]["target_id"] == 2, edges

    def test_import_with_no_module_target_emits_no_edge(self, tmp_path):
        """``yaml`` is ONLY indexed as a local variable in tests/. The
        resolver MUST emit NO edge — better than a phantom edge to the
        local."""
        project = _build_project(
            tmp_path,
            {"src/foo.py": "import yaml\n\ndef use_yaml():\n    return 0\n"},
        )
        symbols = [
            _sym(1, "use_yaml", file_path="src/foo.py", line_start=3, line_end=4),
            # ONLY a local variable, no module symbol — the W181 case.
            _sym(2, "yaml", file_path="tests/test_runtime.py", kind="variable",
                 line_start=340, line_end=340),
        ]
        symbols_by_name, files_by_path = _build_inputs(symbols)
        refs = [
            {
                "source_name": "use_yaml",
                "target_name": "yaml",
                "kind": "import",
                "line": 1,
                "source_file": "src/foo.py",
                "import_path": "yaml",
            },
        ]
        drop_stats: dict = {}
        edges = resolve_references(
            refs, symbols_by_name, files_by_path,
            project_root=str(project), drop_stats=drop_stats,
        )
        # No edge at all — not dropped by W167 text-check, not emitted
        # in the first place. The W167 counter should therefore be 0.
        assert not any(e["kind"] == "import" for e in edges), edges
        assert drop_stats.get("dropped_import_edges", 0) == 0

    def test_import_property_candidate_skipped(self, tmp_path):
        """``from datetime import timezone`` — the only indexed
        ``timezone`` symbol is a class property
        ``_FrozenDatetime.timezone`` in a test file. The resolver MUST
        skip it (property is never an import target)."""
        project = _build_project(
            tmp_path,
            {
                "src/cga.py": (
                    "from datetime import timezone\n"
                    "\n"
                    "PREDICATE_TYPE = timezone.utc\n"
                ),
            },
        )
        symbols = [
            _sym(1, "PREDICATE_TYPE", file_path="src/cga.py",
                 line_start=3, line_end=3, kind="constant"),
            _sym(2, "timezone", file_path="tests/test_cli_responses_write.py",
                 qn="_FrozenDatetime.timezone", kind="property",
                 line_start=27, line_end=27),
        ]
        symbols_by_name, files_by_path = _build_inputs(symbols)
        refs = [
            {
                "source_name": "PREDICATE_TYPE",
                "target_name": "timezone",
                "kind": "import",
                "line": 1,
                "source_file": "src/cga.py",
                "import_path": "datetime.timezone",
            },
        ]
        edges = resolve_references(
            refs, symbols_by_name, files_by_path,
            project_root=str(project),
        )
        assert not any(e["kind"] == "import" for e in edges), edges

    def test_import_path_preference(self, tmp_path):
        """When the source does ``from roam.foo import bar`` AND there are
        two ``bar`` symbols — one a real function in ``src/roam/foo.py``,
        one a local variable in ``tests/test_x.py`` — the resolver MUST
        pick the src/ function (it's both the right kind AND the right
        path)."""
        project = _build_project(
            tmp_path,
            {
                "src/x.py": "from roam.foo import bar\n\ndef use():\n    return bar()\n",
            },
        )
        symbols = [
            _sym(1, "use", file_path="src/x.py", line_start=3, line_end=4),
            _sym(2, "bar", file_path="src/roam/foo.py",
                 qn="roam.foo.bar", kind="function", line_start=1, line_end=2),
            _sym(3, "bar", file_path="tests/test_x.py",
                 kind="variable", line_start=10, line_end=10),
        ]
        symbols_by_name, files_by_path = _build_inputs(symbols)
        refs = [
            {
                "source_name": "use",
                "target_name": "bar",
                "kind": "import",
                "line": 1,
                "source_file": "src/x.py",
                "import_path": "roam.foo.bar",
            },
        ]
        edges = resolve_references(
            refs, symbols_by_name, files_by_path,
            project_root=str(project),
        )
        import_edges = [e for e in edges if e["kind"] == "import"]
        assert len(import_edges) == 1, edges
        assert import_edges[0]["target_id"] == 2, edges

    def test_existing_resolution_unchanged_for_normal_case(self, tmp_path):
        """Regression: ``from datetime import timezone`` where the DB
        contains a function/module-shape symbol named ``timezone`` (the
        normal indexed case) MUST still resolve correctly. The W181
        filter only drops kinds that are never legitimate import
        targets; ``function``/``class``/``module``/``constant`` pass
        through unchanged."""
        project = _build_project(
            tmp_path,
            {
                "src/foo.py": "from datetime import timezone\n\nTZ = timezone.utc\n",
            },
        )
        symbols = [
            _sym(1, "TZ", file_path="src/foo.py", line_start=3,
                 line_end=3, kind="constant"),
            # The "real" datetime.timezone surrogate — function-kind,
            # qualified as ``datetime.timezone``. This represents a
            # well-indexed third-party / stdlib module symbol.
            _sym(2, "timezone", file_path="stdlib/datetime.py",
                 qn="datetime.timezone", kind="class",
                 line_start=100, line_end=200),
        ]
        symbols_by_name, files_by_path = _build_inputs(symbols)
        refs = [
            {
                "source_name": "TZ",
                "target_name": "timezone",
                "kind": "import",
                "line": 1,
                "source_file": "src/foo.py",
                "import_path": "datetime.timezone",
            },
        ]
        edges = resolve_references(
            refs, symbols_by_name, files_by_path,
            project_root=str(project),
        )
        import_edges = [e for e in edges if e["kind"] == "import"]
        assert len(import_edges) == 1, edges
        assert import_edges[0]["target_id"] == 2, edges

    def test_resolver_drops_post_W158_filter(self, tmp_path, monkeypatch):
        """Confirm the W181 upstream fix is sufficient: with the W158
        laws-miner filter bypassed, the resolver still emits zero
        phantom edges for the original smoking-gun fixture.

        The W158 filter at :mod:`roam.laws.miner._mine_import_laws`
        operates on ``edges`` already in the DB. Here we test the
        layer *below* it: that no phantom edge is even produced.
        The W158 filter therefore has nothing to filter out — its
        load-bearingness is now zero on this fixture.
        """
        project = _build_project(
            tmp_path,
            {
                "src/seeds.py": "import yaml\n\ndef seed():\n    return 1\n",
            },
        )
        symbols = [
            _sym(1, "seed", file_path="src/seeds.py",
                 line_start=3, line_end=4),
            # The original W158 smoking-gun: yaml only exists as a
            # local variable inside a test file.
            _sym(2, "yaml", file_path="tests/test_runtime_score.py",
                 kind="variable", line_start=340, line_end=340),
        ]
        symbols_by_name, files_by_path = _build_inputs(symbols)
        refs = [
            {
                "source_name": "seed",
                "target_name": "yaml",
                "kind": "import",
                "line": 1,
                "source_file": "src/seeds.py",
                "import_path": "yaml",
            },
        ]
        # Sanity-check the bypass: even if W158/W167 were absent, W181
        # alone produces zero import edges for this fixture. We can't
        # easily "monkey-patch off" W158 from within this module
        # because W158 lives in roam.laws.miner (post-DB) — but we can
        # bypass W167 by setting the W167 text-check to accept all
        # edges, which simulates "no defence-in-depth in this layer".
        # If the test passes WITHOUT W167's drop, W181 is the
        # load-bearing layer.
        def _accept_all(edges, names_map, counter):
            counter["dropped_import_edges"] = 0
            for e in edges:
                e.pop("_target_name", None)
                e.pop("_source_path", None)
            return edges

        monkeypatch.setattr(relations_mod, "_verify_import_edges", _accept_all)

        edges = resolve_references(
            refs, symbols_by_name, files_by_path,
            project_root=str(project),
        )
        # W181 alone is sufficient.
        assert not any(e["kind"] == "import" for e in edges), edges
