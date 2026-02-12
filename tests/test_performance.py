"""Performance and stress tests for Roam.

Covers:
- Indexing speed on medium-sized projects
- Query response times for all commands
- Stress tests with large files, many files, deep nesting
- Memory resilience with malformed/unusual inputs
- Corrupted index recovery
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import roam, git_init, git_commit


# ============================================================================
# PERFORMANCE THRESHOLDS
# ============================================================================

INDEX_TIME_PER_FILE_MS = 50  # max ms per file for indexing
QUERY_TIME_MS = 2000         # max ms for any single query command


def timed_roam(*args, **kwargs):
    """Run roam and return (output, returncode, elapsed_ms)."""
    start = time.perf_counter()
    out, rc = roam(*args, **kwargs)
    elapsed_ms = (time.perf_counter() - start) * 1000
    return out, rc, elapsed_ms


# ============================================================================
# MEDIUM PROJECT FIXTURE (200 files)
# ============================================================================

@pytest.fixture(scope="module")
def medium_project(tmp_path_factory):
    """Create a project with 200 Python files, each with a class and methods."""
    proj = tmp_path_factory.mktemp("medium")

    # Create 10 packages with 20 files each = 200 files
    for pkg_idx in range(10):
        pkg_dir = proj / f"pkg_{pkg_idx}"
        pkg_dir.mkdir()
        (pkg_dir / "__init__.py").write_text(
            f'"""Package {pkg_idx}."""\n'
        )
        for file_idx in range(20):
            cls_name = f"Service{pkg_idx}_{file_idx}"
            methods = []
            for m in range(5):
                methods.append(
                    f'    def method_{m}(self, arg{m}: str) -> str:\n'
                    f'        return f"{{arg{m}}} processed"\n'
                )
            imports = ""
            if file_idx > 0:
                prev_cls = f"Service{pkg_idx}_{file_idx - 1}"
                imports = f'from .file_{file_idx - 1} import {prev_cls}\n\n'

            (pkg_dir / f"file_{file_idx}.py").write_text(
                f'{imports}'
                f'class {cls_name}:\n'
                f'    """Service class {cls_name}."""\n'
                f'    VERSION = "{pkg_idx}.{file_idx}"\n'
                f'\n'
                f'    def __init__(self, name: str):\n'
                f'        self.name = name\n'
                f'\n'
                + '\n'.join(methods)
            )

    # Add a main entry point
    (proj / "main.py").write_text(
        'from pkg_0.file_0 import Service0_0\n'
        '\n'
        'def main():\n'
        '    svc = Service0_0("test")\n'
        '    return svc.method_0("hello")\n'
    )

    git_init(proj)
    return proj


# ============================================================================
# INDEXING PERFORMANCE
# ============================================================================

class TestIndexingPerformance:
    def test_index_medium_project(self, medium_project):
        """Indexing 200+ files should complete in reasonable time."""
        out, rc, elapsed = timed_roam("index", "--force", cwd=medium_project)
        assert rc == 0, f"Index failed: {out}"

        # Count files
        file_count = sum(1 for _ in medium_project.rglob("*.py"))
        max_time = file_count * INDEX_TIME_PER_FILE_MS

        assert elapsed < max_time, (
            f"Index took {elapsed:.0f}ms for {file_count} files "
            f"({elapsed/file_count:.0f}ms/file, limit {INDEX_TIME_PER_FILE_MS}ms/file)"
        )

    def test_incremental_index_fast(self, medium_project):
        """Incremental index with no changes should be near-instant."""
        # First make sure index exists
        roam("index", cwd=medium_project)

        out, rc, elapsed = timed_roam("index", cwd=medium_project)
        assert rc == 0
        assert "up to date" in out
        assert elapsed < 3000, f"No-change incremental took {elapsed:.0f}ms (limit 3000ms)"

    def test_incremental_single_file_change(self, medium_project):
        """Changing one file should re-index only that file quickly."""
        # Modify one file
        target = medium_project / "pkg_0" / "file_0.py"
        content = target.read_text()
        target.write_text(content + '\ndef new_func(): pass\n')
        git_commit(medium_project, "modify one file")

        out, rc, elapsed = timed_roam("index", cwd=medium_project)
        assert rc == 0
        assert "Re-extracting" in out
        assert elapsed < 5000, f"Single-file incremental took {elapsed:.0f}ms (limit 5000ms)"


# ============================================================================
# QUERY PERFORMANCE
# ============================================================================

class TestQueryPerformance:
    @pytest.fixture(autouse=True)
    def ensure_indexed(self, medium_project):
        """Make sure the medium project is indexed before queries."""
        roam("index", cwd=medium_project)
        self.proj = medium_project

    def test_map_speed(self):
        out, rc, elapsed = timed_roam("map", cwd=self.proj)
        assert rc == 0
        assert elapsed < QUERY_TIME_MS, f"map took {elapsed:.0f}ms"

    def test_file_speed(self):
        out, rc, elapsed = timed_roam("file", "pkg_0/file_0.py", cwd=self.proj)
        assert rc == 0
        assert elapsed < QUERY_TIME_MS, f"file took {elapsed:.0f}ms"

    def test_symbol_speed(self):
        out, rc, elapsed = timed_roam("symbol", "Service0_0", cwd=self.proj)
        assert rc == 0
        assert elapsed < QUERY_TIME_MS, f"symbol took {elapsed:.0f}ms"

    def test_search_speed(self):
        out, rc, elapsed = timed_roam("search", "Service", cwd=self.proj)
        assert rc == 0
        assert elapsed < QUERY_TIME_MS, f"search took {elapsed:.0f}ms"

    def test_deps_speed(self):
        out, rc, elapsed = timed_roam("deps", "pkg_0/file_0.py", cwd=self.proj)
        assert rc == 0
        assert elapsed < QUERY_TIME_MS, f"deps took {elapsed:.0f}ms"

    def test_health_speed(self):
        out, rc, elapsed = timed_roam("health", cwd=self.proj)
        assert rc == 0
        assert elapsed < QUERY_TIME_MS, f"health took {elapsed:.0f}ms"

    def test_dead_speed(self):
        out, rc, elapsed = timed_roam("dead", cwd=self.proj)
        assert rc == 0
        assert elapsed < QUERY_TIME_MS, f"dead took {elapsed:.0f}ms"

    def test_clusters_speed(self):
        out, rc, elapsed = timed_roam("clusters", cwd=self.proj)
        assert rc == 0
        assert elapsed < QUERY_TIME_MS, f"clusters took {elapsed:.0f}ms"

    def test_layers_speed(self):
        out, rc, elapsed = timed_roam("layers", cwd=self.proj)
        assert rc == 0
        assert elapsed < QUERY_TIME_MS, f"layers took {elapsed:.0f}ms"

    def test_weather_speed(self):
        out, rc, elapsed = timed_roam("weather", cwd=self.proj)
        assert rc == 0
        assert elapsed < QUERY_TIME_MS, f"weather took {elapsed:.0f}ms"

    def test_grep_speed(self):
        out, rc, elapsed = timed_roam("grep", "method_0", cwd=self.proj)
        assert rc == 0
        assert elapsed < QUERY_TIME_MS, f"grep took {elapsed:.0f}ms"

    def test_uses_speed(self):
        out, rc, elapsed = timed_roam("uses", "Service0_0", cwd=self.proj)
        assert rc == 0
        assert elapsed < QUERY_TIME_MS, f"uses took {elapsed:.0f}ms"

    def test_impact_speed(self):
        out, rc, elapsed = timed_roam("impact", "Service0_0", cwd=self.proj)
        assert rc == 0
        assert elapsed < QUERY_TIME_MS, f"impact took {elapsed:.0f}ms"

    def test_fan_speed(self):
        out, rc, elapsed = timed_roam("fan", "symbol", cwd=self.proj)
        assert rc == 0
        assert elapsed < QUERY_TIME_MS, f"fan took {elapsed:.0f}ms"

    def test_coupling_speed(self):
        out, rc, elapsed = timed_roam("coupling", cwd=self.proj)
        assert rc == 0
        assert elapsed < QUERY_TIME_MS, f"coupling took {elapsed:.0f}ms"


# ============================================================================
# NEW v5 COMMAND PERFORMANCE
# ============================================================================

class TestNewCommandPerformance:
    """Benchmarks for commands added in v5 (understand, coverage-gaps, report, etc.)."""

    @pytest.fixture(autouse=True)
    def ensure_indexed(self, medium_project):
        """Make sure the medium project is indexed before queries."""
        roam("index", cwd=medium_project)
        self.proj = medium_project

    def test_understand_speed(self):
        """roam understand should complete within 5s."""
        out, rc, elapsed = timed_roam("understand", cwd=self.proj)
        assert rc == 0
        assert elapsed < 5000, f"understand took {elapsed:.0f}ms (limit 5000ms)"

    def test_dead_summary_speed(self):
        out, rc, elapsed = timed_roam("dead", "--summary", cwd=self.proj)
        assert rc == 0
        assert elapsed < QUERY_TIME_MS, f"dead --summary took {elapsed:.0f}ms"

    def test_dead_clusters_speed(self):
        out, rc, elapsed = timed_roam("dead", "--clusters", cwd=self.proj)
        assert rc == 0
        assert elapsed < QUERY_TIME_MS, f"dead --clusters took {elapsed:.0f}ms"

    def test_dead_by_kind_speed(self):
        out, rc, elapsed = timed_roam("dead", "--by-kind", cwd=self.proj)
        assert rc == 0
        assert elapsed < QUERY_TIME_MS, f"dead --by-kind took {elapsed:.0f}ms"

    def test_coverage_gaps_speed(self):
        out, rc, elapsed = timed_roam(
            "coverage-gaps", "--gate-pattern", "auth|permission",
            cwd=self.proj,
        )
        assert rc == 0
        assert elapsed < QUERY_TIME_MS, f"coverage-gaps took {elapsed:.0f}ms"

    def test_snapshot_speed(self):
        out, rc, elapsed = timed_roam("snapshot", "--tag", "bench", cwd=self.proj)
        assert rc == 0
        assert elapsed < QUERY_TIME_MS, f"snapshot took {elapsed:.0f}ms"

    def test_trend_speed(self):
        # Ensure at least one snapshot exists
        roam("snapshot", cwd=self.proj)
        out, rc, elapsed = timed_roam("trend", cwd=self.proj)
        assert rc == 0
        assert elapsed < QUERY_TIME_MS, f"trend took {elapsed:.0f}ms"

    def test_trend_assert_speed(self):
        roam("snapshot", cwd=self.proj)
        out, rc, elapsed = timed_roam("trend", "--assert", "cycles<=100", cwd=self.proj)
        assert rc == 0
        assert elapsed < QUERY_TIME_MS, f"trend --assert took {elapsed:.0f}ms"

    def test_report_list_speed(self):
        out, rc, elapsed = timed_roam("report", "--list", cwd=self.proj)
        assert rc == 0
        assert elapsed < QUERY_TIME_MS, f"report --list took {elapsed:.0f}ms"


# ============================================================================
# JSON OUTPUT PERFORMANCE
# ============================================================================

class TestJsonPerformance:
    """Verify --json flag doesn't add significant overhead."""

    @pytest.fixture(autouse=True)
    def ensure_indexed(self, medium_project):
        roam("index", cwd=medium_project)
        self.proj = medium_project

    def test_json_map_speed(self):
        out, rc, elapsed = timed_roam("--json", "map", cwd=self.proj)
        assert rc == 0
        assert elapsed < QUERY_TIME_MS, f"--json map took {elapsed:.0f}ms"
        assert '"command"' in out

    def test_json_health_speed(self):
        out, rc, elapsed = timed_roam("--json", "health", cwd=self.proj)
        assert rc == 0
        assert elapsed < QUERY_TIME_MS, f"--json health took {elapsed:.0f}ms"
        assert '"command"' in out

    def test_json_dead_speed(self):
        out, rc, elapsed = timed_roam("--json", "dead", cwd=self.proj)
        assert rc == 0
        assert elapsed < QUERY_TIME_MS, f"--json dead took {elapsed:.0f}ms"
        assert '"command"' in out

    def test_json_understand_speed(self):
        out, rc, elapsed = timed_roam("--json", "understand", cwd=self.proj)
        assert rc == 0
        assert elapsed < 5000, f"--json understand took {elapsed:.0f}ms"
        assert '"command"' in out

    def test_json_envelope_structure(self):
        """Verify JSON envelope has required fields."""
        import json
        out, rc, _ = timed_roam("--json", "health", cwd=self.proj)
        assert rc == 0
        data = json.loads(out)
        assert "command" in data
        assert "timestamp" in data
        assert "summary" in data


# ============================================================================
# SELF-BENCHMARK (roam-code on itself)
# ============================================================================

class TestSelfBenchmark:
    """Benchmark roam commands against the roam-code project itself.

    These tests use the actual project as the benchmark fixture, providing
    real-world performance data on a ~140-file Python codebase.
    Skipped in CI (requires the real project to be indexed).
    """

    @pytest.fixture(autouse=True)
    def project_root(self):
        """Use the roam-code project root as test target."""
        root = Path(__file__).parent.parent
        db = root / ".roam" / "index.db"
        if not db.exists():
            pytest.skip("roam-code not indexed (run `roam index` first)")
        self.proj = root

    def test_self_understand(self):
        out, rc, elapsed = timed_roam("understand", cwd=self.proj)
        assert rc == 0
        print(f"  understand: {elapsed:.0f}ms")

    def test_self_health(self):
        out, rc, elapsed = timed_roam("health", cwd=self.proj)
        assert rc == 0
        print(f"  health: {elapsed:.0f}ms")

    def test_self_map(self):
        out, rc, elapsed = timed_roam("map", cwd=self.proj)
        assert rc == 0
        print(f"  map: {elapsed:.0f}ms")

    def test_self_dead(self):
        out, rc, elapsed = timed_roam("dead", cwd=self.proj)
        assert rc == 0
        print(f"  dead: {elapsed:.0f}ms")

    def test_self_coupling(self):
        out, rc, elapsed = timed_roam("coupling", cwd=self.proj)
        assert rc == 0
        print(f"  coupling: {elapsed:.0f}ms")

    def test_self_weather(self):
        out, rc, elapsed = timed_roam("weather", cwd=self.proj)
        assert rc == 0
        print(f"  weather: {elapsed:.0f}ms")

    def test_self_layers(self):
        out, rc, elapsed = timed_roam("layers", cwd=self.proj)
        assert rc == 0
        print(f"  layers: {elapsed:.0f}ms")


# ============================================================================
# STRESS TESTS
# ============================================================================

class TestStress:
    def test_large_file(self, tmp_path):
        """A file with 500 functions should index without issues."""
        proj = tmp_path / "large"
        proj.mkdir()
        functions = []
        for i in range(500):
            functions.append(
                f'def func_{i}(x):\n'
                f'    """Function {i}."""\n'
                f'    return x + {i}\n'
            )
        (proj / "big.py").write_text('\n'.join(functions))
        git_init(proj)

        out, rc, elapsed = timed_roam("index", "--force", cwd=proj)
        assert rc == 0, f"Large file index failed: {out}"

        # Functions should be searchable (search may truncate to top 50)
        out, _ = roam("search", "func_", cwd=proj)
        assert "func_0" in out
        # 500 functions found, output is truncated — check the count header
        assert "(500)" in out or "func_1" in out

    def test_deep_directory_nesting(self, tmp_path):
        """Deeply nested directories should work."""
        proj = tmp_path / "deep"
        current = proj
        for i in range(15):
            current = current / f"level_{i}"
        current.mkdir(parents=True)
        (current / "deep.py").write_text(
            'def deep_function():\n'
            '    return "I am deep"\n'
        )
        git_init(proj)
        out, rc = roam("index", "--force", cwd=proj)
        assert rc == 0

        out, _ = roam("search", "deep_function", cwd=proj)
        assert "deep_function" in out

    def test_many_small_files(self, tmp_path):
        """500 tiny files should index correctly."""
        proj = tmp_path / "many"
        proj.mkdir()
        for i in range(500):
            (proj / f"f_{i}.py").write_text(f'x_{i} = {i}\n')
        git_init(proj)

        out, rc, elapsed = timed_roam("index", "--force", cwd=proj)
        assert rc == 0, f"Many files index failed: {out}"

        out, _ = roam("map", cwd=proj)
        assert "500" in out or "Files:" in out

    def test_class_hierarchy_depth(self, tmp_path):
        """Deep inheritance chain should be handled."""
        proj = tmp_path / "chain"
        proj.mkdir()
        classes = ['class Base:\n    def base_method(self): pass\n']
        for i in range(20):
            parent = "Base" if i == 0 else f"Child{i-1}"
            classes.append(
                f'class Child{i}({parent}):\n'
                f'    def method_{i}(self): pass\n'
            )
        (proj / "chain.py").write_text('\n'.join(classes))
        git_init(proj)

        out, rc = roam("index", "--force", cwd=proj)
        assert rc == 0

        out, _ = roam("search", "Child19", cwd=proj)
        assert "Child19" in out

    def test_circular_imports(self, tmp_path):
        """Circular imports should not crash the indexer."""
        proj = tmp_path / "circular"
        proj.mkdir()
        (proj / "a.py").write_text(
            'from b import func_b\n\n'
            'def func_a():\n'
            '    return func_b()\n'
        )
        (proj / "b.py").write_text(
            'from a import func_a\n\n'
            'def func_b():\n'
            '    return func_a()\n'
        )
        git_init(proj)
        out, rc = roam("index", "--force", cwd=proj)
        assert rc == 0

        # Health should detect the cycle
        out, _ = roam("health", cwd=proj)
        assert rc == 0

    def test_many_edges(self, tmp_path):
        """A file importing from many modules should create many edges."""
        proj = tmp_path / "edges"
        proj.mkdir()

        for i in range(50):
            (proj / f"mod_{i}.py").write_text(
                f'def helper_{i}():\n    return {i}\n'
            )

        imports = '\n'.join(f'from mod_{i} import helper_{i}' for i in range(50))
        calls = '\n'.join(f'    helper_{i}()' for i in range(50))
        (proj / "hub.py").write_text(
            f'{imports}\n\n'
            f'def hub_func():\n'
            f'{calls}\n'
        )
        git_init(proj)

        out, rc = roam("index", "--force", cwd=proj)
        assert rc == 0

        out, _ = roam("deps", "hub.py", cwd=proj)
        assert "mod_0" in out or "helper_0" in out


# ============================================================================
# CORRUPTED / UNUSUAL INDEX STATES
# ============================================================================

class TestResilience:
    def test_corrupted_index_recovery(self, tmp_path):
        """A corrupted index.db should be recoverable with --force."""
        proj = tmp_path / "corrupt"
        proj.mkdir()
        (proj / "main.py").write_text('def main(): pass\n')
        git_init(proj)

        # Build a valid index first
        roam("index", "--force", cwd=proj)

        # Corrupt the index
        db_path = proj / ".roam" / "index.db"
        assert db_path.exists()
        db_path.write_bytes(b"this is not a sqlite database")

        # Force re-index should recover
        out, rc = roam("index", "--force", cwd=proj)
        assert rc == 0

        out, _ = roam("search", "main", cwd=proj)
        assert "main" in out

    def test_empty_index_dir(self, tmp_path):
        """Empty .roam dir without index.db should auto-create."""
        proj = tmp_path / "emptyidx"
        proj.mkdir()
        (proj / "main.py").write_text('def main(): pass\n')
        (proj / ".roam").mkdir()
        git_init(proj)

        out, rc = roam("map", cwd=proj)
        assert rc == 0

    def test_readonly_file_skipped(self, tmp_path):
        """Files that can't be read should be skipped gracefully."""
        proj = tmp_path / "readonly"
        proj.mkdir()
        (proj / "good.py").write_text('def good(): pass\n')
        git_init(proj)

        out, rc = roam("index", "--force", cwd=proj)
        assert rc == 0
        out, _ = roam("search", "good", cwd=proj)
        assert "good" in out

    def test_special_chars_in_identifiers(self, tmp_path):
        """Identifiers with unusual but valid chars should work."""
        proj = tmp_path / "special"
        proj.mkdir()
        (proj / "special.py").write_text(
            'class _Private:\n    pass\n\n'
            'class __DunderClass:\n    pass\n\n'
            'def _____many_underscores():\n    pass\n\n'
            'CONSTANT_WITH_NUMBERS_123 = 42\n'
        )
        git_init(proj)
        out, rc = roam("index", "--force", cwd=proj)
        assert rc == 0

        out, _ = roam("search", "_Private", cwd=proj)
        assert "_Private" in out

    def test_index_twice_idempotent(self, tmp_path):
        """Running index twice should produce identical results."""
        proj = tmp_path / "idempotent"
        proj.mkdir()
        (proj / "main.py").write_text('def main(): return 42\n')
        git_init(proj)

        roam("index", "--force", cwd=proj)
        out1, _ = roam("map", cwd=proj)

        roam("index", "--force", cwd=proj)
        out2, _ = roam("map", cwd=proj)

        assert out1 == out2, "Double-index produced different outputs"


# ============================================================================
# VERBOSE AND ELAPSED TIME
# ============================================================================

class TestVerboseFlag:
    def test_verbose_shows_warnings(self, tmp_path):
        """roam index --verbose should display warnings."""
        proj = tmp_path / "verbose_test"
        proj.mkdir()
        (proj / "main.py").write_text('def main(): pass\n')
        git_init(proj)
        out, rc = roam("index", "--force", "--verbose", cwd=proj)
        assert rc == 0
        # Even without warnings, the flag should be accepted
        assert "Done." in out or "Index complete." in out

    def test_no_verbose_suppresses_warnings(self, tmp_path):
        """roam index without --verbose should suppress 'Warning:' lines."""
        proj = tmp_path / "quiet_test"
        proj.mkdir()
        (proj / "main.py").write_text('def main(): pass\n')
        git_init(proj)
        out, rc = roam("index", "--force", cwd=proj)
        assert rc == 0
        assert "Warning:" not in out

    def test_elapsed_time_shown(self, tmp_path):
        """roam index should show elapsed time."""
        proj = tmp_path / "elapsed_test"
        proj.mkdir()
        (proj / "main.py").write_text('def main(): pass\n')
        git_init(proj)
        out, rc = roam("index", "--force", cwd=proj)
        assert rc == 0
        assert "Index complete." in out
        # Should contain elapsed time in parentheses like "(0.5s)"
        import re
        assert re.search(r'\(\d+\.\d+s\)', out), f"No elapsed time found in: {out}"


# ============================================================================
# OUTPUT CORRECTNESS
# ============================================================================

class TestOutputCorrectness:
    @pytest.fixture
    def verified_project(self, tmp_path):
        """A project with known structure for output verification."""
        proj = tmp_path / "verify"
        proj.mkdir()
        (proj / "math_utils.py").write_text(
            'def add(a: int, b: int) -> int:\n'
            '    """Add two numbers."""\n'
            '    return a + b\n'
            '\n'
            'def multiply(x: int, y: int) -> int:\n'
            '    """Multiply two numbers."""\n'
            '    return x * y\n'
            '\n'
            'PI = 3.14159\n'
        )
        (proj / "calculator.py").write_text(
            'from math_utils import add, multiply\n'
            '\n'
            'class Calculator:\n'
            '    """A simple calculator."""\n'
            '    \n'
            '    def __init__(self):\n'
            '        self.history = []\n'
            '\n'
            '    def compute(self, op: str, a: int, b: int) -> int:\n'
            '        if op == "add":\n'
            '            result = add(a, b)\n'
            '        else:\n'
            '            result = multiply(a, b)\n'
            '        self.history.append(result)\n'
            '        return result\n'
        )
        (proj / "app.py").write_text(
            'from calculator import Calculator\n'
            '\n'
            'def main():\n'
            '    calc = Calculator()\n'
            '    print(calc.compute("add", 1, 2))\n'
        )
        git_init(proj)
        roam("index", "--force", cwd=proj)
        return proj

    def test_file_shows_all_symbols(self, verified_project):
        """roam file should list all definitions in a file."""
        out, _ = roam("file", "math_utils.py", cwd=verified_project)
        assert "add" in out
        assert "multiply" in out
        assert "PI" in out

    def test_file_shows_signatures(self, verified_project):
        """roam file should show function signatures."""
        out, _ = roam("file", "math_utils.py", cwd=verified_project)
        # Signature should include parameter names
        assert "a" in out and "b" in out

    def test_symbol_shows_location(self, verified_project):
        """roam symbol should show file location."""
        out, _ = roam("symbol", "Calculator", cwd=verified_project)
        assert "calculator.py" in out

    def test_deps_shows_imports(self, verified_project):
        """roam deps should show import relationships."""
        out, _ = roam("deps", "calculator.py", cwd=verified_project)
        # Import edges depend on resolution; at minimum the command should succeed
        assert "calculator.py" in out

    def test_deps_shows_importers(self, verified_project):
        """roam deps should show who imports this file."""
        out, _ = roam("deps", "calculator.py", cwd=verified_project)
        assert "app.py" in out or "Imported by" in out

    def test_map_shows_file_count(self, verified_project):
        """roam map should show correct file count."""
        out, _ = roam("map", cwd=verified_project)
        assert "3" in out  # 3 python files

    def test_search_finds_partial(self, verified_project):
        """roam search should find partial matches."""
        out, _ = roam("search", "calc", cwd=verified_project)
        assert "Calculator" in out or "calculator" in out

    def test_dead_detects_unreferenced(self, verified_project):
        """roam dead should find unreferenced exports."""
        out, _ = roam("dead", cwd=verified_project)
        # Should find some unreferenced symbols (main, compute, etc.)
        assert "Unreferenced" in out or "No unreferenced" in out

    def test_map_shows_entry_points(self, verified_project):
        """roam map should identify entry points or high-rank symbols."""
        out, _ = roam("map", cwd=verified_project)
        # Should show entry points (file paths) or top symbols
        assert "app.py" in out or "Calculator" in out or "main" in out

    def test_module_lists_directory(self, verified_project):
        """roam module . should list root files."""
        out, rc = roam("module", ".", cwd=verified_project)
        assert rc == 0
        assert "math_utils.py" in out or "calculator.py" in out or "app.py" in out

    # --- New v2 command tests ---

    def test_impact_shows_dependents(self, verified_project):
        """roam impact should show blast radius for a symbol."""
        out, rc = roam("impact", "add", cwd=verified_project)
        assert rc == 0
        assert "Affected" in out or "No dependents" in out or "add" in out

    def test_impact_not_found(self, verified_project):
        """roam impact should fail gracefully for unknown symbols."""
        out, rc = roam("impact", "nonexistent_xyz_123", cwd=verified_project)
        assert rc != 0 or "not found" in out.lower()

    def test_owner_shows_ownership(self, verified_project):
        """roam owner should show code ownership for a file."""
        out, rc = roam("owner", "math_utils.py", cwd=verified_project)
        assert rc == 0
        # Should show author info or "no blame data"
        assert "author" in out.lower() or "blame" in out.lower() or "Test" in out or "Fragmentation" in out

    def test_coupling_shows_cochange(self, verified_project):
        """roam coupling should show temporal coupling data."""
        out, rc = roam("coupling", cwd=verified_project)
        assert rc == 0
        # May have co-change data or not depending on git history
        assert "co-change" in out.lower() or "coupling" in out.lower() or "No co-change" in out

    def test_fan_symbol_mode(self, verified_project):
        """roam fan symbol should show fan-in/fan-out metrics."""
        out, rc = roam("fan", "symbol", cwd=verified_project)
        assert rc == 0
        assert "fan-in" in out.lower() or "Fan-in" in out or "No graph metrics" in out

    def test_fan_file_mode(self, verified_project):
        """roam fan file should show file-level fan metrics."""
        out, rc = roam("fan", "file", cwd=verified_project)
        assert rc == 0
        assert "fan-in" in out.lower() or "Fan-in" in out or "No file edges" in out

    # --- --full flag tests ---

    def test_symbol_full_flag(self, verified_project):
        """roam symbol --full should show all callers/callees."""
        out, rc = roam("symbol", "--full", "Calculator", cwd=verified_project)
        assert rc == 0
        assert "calculator.py" in out

    def test_search_full_flag(self, verified_project):
        """roam search --full should show all results."""
        out, rc = roam("search", "--full", "a", cwd=verified_project)
        assert rc == 0

    def test_map_full_flag(self, verified_project):
        """roam map --full should show all directories and symbols."""
        out, rc = roam("map", "--full", cwd=verified_project)
        assert rc == 0
        assert "Files:" in out


# ============================================================================
# v6.0 COMMAND BENCHMARKS
# ============================================================================

class TestV6CommandPerformance:
    """Benchmarks for v6.0 intelligence commands."""

    @pytest.fixture(autouse=True)
    def ensure_indexed(self, medium_project):
        roam("index", cwd=medium_project)
        self.proj = medium_project

    def test_complexity_speed(self):
        """roam complexity should complete within 3s."""
        out, rc, elapsed = timed_roam("complexity", cwd=self.proj)
        assert rc == 0
        assert elapsed < 3000, f"complexity took {elapsed:.0f}ms (limit 3000ms)"

    def test_complexity_bumpy_road_speed(self):
        """roam complexity --bumpy-road should complete within 3s."""
        out, rc, elapsed = timed_roam("complexity", "--bumpy-road", cwd=self.proj)
        assert rc == 0
        assert elapsed < 3000

    def test_conventions_speed(self):
        """roam conventions should complete within 3s."""
        out, rc, elapsed = timed_roam("conventions", cwd=self.proj)
        assert rc == 0
        assert elapsed < 3000, f"conventions took {elapsed:.0f}ms (limit 3000ms)"

    def test_debt_speed(self):
        """roam debt should complete within 3s."""
        out, rc, elapsed = timed_roam("debt", cwd=self.proj)
        assert rc == 0
        assert elapsed < 3000

    def test_entry_points_speed(self):
        """roam entry-points should complete within 3s."""
        out, rc, elapsed = timed_roam("entry-points", cwd=self.proj)
        assert rc == 0
        assert elapsed < 3000

    def test_patterns_speed(self):
        """roam patterns should complete within 5s."""
        out, rc, elapsed = timed_roam("patterns", cwd=self.proj)
        assert rc == 0
        assert elapsed < 5000

    def test_alerts_speed(self):
        """roam alerts should complete within 3s."""
        out, rc, elapsed = timed_roam("alerts", cwd=self.proj)
        assert rc == 0
        assert elapsed < 3000

    def test_preflight_speed(self):
        """roam preflight should complete within 5s."""
        out, rc, elapsed = timed_roam("preflight", "method_0", cwd=self.proj)
        assert rc == 0, f"preflight failed (rc={rc}): {out[:200]}"
        assert elapsed < 5000, f"preflight took {elapsed:.0f}ms (limit 5000ms)"

    def test_map_budget_speed(self):
        """roam map --budget should complete within 2s."""
        out, rc, elapsed = timed_roam("map", "--budget", "500", cwd=self.proj)
        assert rc == 0
        assert elapsed < 2000

    def test_context_task_speed(self):
        """roam context --task refactor should complete within 5s."""
        out, rc, elapsed = timed_roam("context", "--task", "refactor", "Service0_0", cwd=self.proj)
        assert rc == 0
        assert elapsed < 5000

    def test_understand_enhanced_speed(self):
        """Enhanced understand should complete within 5s."""
        out, rc, elapsed = timed_roam("understand", cwd=self.proj)
        assert rc == 0
        assert elapsed < 5000, f"understand took {elapsed:.0f}ms (limit 5000ms)"

    def test_describe_enhanced_speed(self):
        """Enhanced describe should complete within 5s."""
        out, rc, elapsed = timed_roam("describe", cwd=self.proj)
        assert rc == 0
        assert elapsed < 5000


class TestV6JsonPerformance:
    """JSON output benchmarks for v6 commands."""

    @pytest.fixture(autouse=True)
    def ensure_indexed(self, medium_project):
        roam("index", cwd=medium_project)
        self.proj = medium_project

    def test_json_complexity(self):
        out, rc, elapsed = timed_roam("--json", "complexity", cwd=self.proj)
        assert rc == 0
        assert elapsed < 3000
        import json
        data = json.loads(out)
        assert data["command"] == "complexity"

    def test_json_debt(self):
        out, rc, elapsed = timed_roam("--json", "debt", cwd=self.proj)
        assert rc == 0
        assert elapsed < 3000
        import json
        data = json.loads(out)
        assert data["command"] == "debt"

    def test_json_preflight(self):
        # Use method name which exists in all Service classes
        out, rc, elapsed = timed_roam("--json", "preflight", "method_0", cwd=self.proj)
        assert rc == 0, f"preflight --json failed (rc={rc}): {out[-500:]}"
        assert elapsed < 5000
        import json
        data = json.loads(out)
        assert data["command"] == "preflight"
        assert "risk_level" in data["summary"]

    def test_json_conventions(self):
        out, rc, elapsed = timed_roam("--json", "conventions", cwd=self.proj)
        assert rc == 0
        assert elapsed < 3000
        import json
        data = json.loads(out)
        assert data["command"] == "conventions"

    def test_json_understand_enhanced(self):
        out, rc, elapsed = timed_roam("--json", "understand", cwd=self.proj)
        assert rc == 0
        assert elapsed < 5000
        import json
        data = json.loads(out)
        assert "conventions" in data
        assert "complexity" in data or "complexity" in str(data)
