"""W1 — module-level describe recall.

Telemetry: "explain the compiler architecture" / "what does the constitution
package do" / "what are the public functions exported by X" leaked to
freeform_explore. describe_file now accepts a module/package NAME target,
resolved to a unique repo file at dispatch time via _resolve_module_names.
"""

from __future__ import annotations

import pytest

from roam.plan.compiler import (
    _classify,
    _extract_describe_module,
)


class TestDescribeModuleClassification:
    @pytest.mark.parametrize(
        "task,expected_name",
        [
            ("explain the compiler architecture", "compiler"),
            ("explain the architecture of the indexer", "indexer"),
            ("explain the purpose of the indexer module", "indexer"),
            ("what does the compiler module do", "compiler"),
            ("what does the constitution package do", "constitution"),
        ],
    )
    def test_module_describe_routes_and_extracts(self, task, expected_name):
        assert _classify(task)[0] == "describe_file"
        assert _extract_describe_module(task) == expected_name

    @pytest.mark.parametrize(
        "task",
        [
            # repo-level phrasings must NOT be treated as module describes
            "explain the overall architecture",
            "what are the layers of this codebase",
            "help me understand the architecture",
            # no describe verb at all
            "refactor the compiler module",
        ],
    )
    def test_repo_level_phrasings_do_not_extract(self, task):
        assert _extract_describe_module(task) is None

    @pytest.mark.parametrize(
        "task",
        [
            "what are the public functions exported by src/roam/atomic_io.py",
            "public functions of src/roam/db/connection.py",
            "what's exported by src/roam/cli.py",
        ],
    )
    def test_exports_phrasings_route_to_describe_file(self, task):
        assert _classify(task)[0] == "describe_file"


class TestModuleNameResolution:
    def test_resolves_unique_stem_in_this_repo(self):
        # Runs inside the roam-code repo — the index knows compiler.py.
        import os

        from roam.plan.compiler import _resolve_module_names

        if not os.path.exists(".roam/index.db"):
            pytest.skip("no index in cwd")
        got = _resolve_module_names("explain the compiler architecture", ".")
        assert got == ["src/roam/plan/compiler.py"]

    def test_unresolvable_name_returns_empty(self):
        from roam.plan.compiler import _resolve_module_names

        got = _resolve_module_names("explain the zzznonexistent module architecture", ".")
        assert got == []
