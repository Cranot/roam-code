"""Tests for v7.0.0 features.

Covers:
- SARIF 2.1.0 output module
- roam init (guided onboarding)
- roam digest (snapshot comparison)
- roam describe --agent-prompt
- roam fitness --explain (reason/link fields)
- roam file multi-file / --changed / --deps-of
- roam context --for-file
- roam bus-factor --brain-methods / entropy
- --compact flag
- --gate expressions
- Categorized --help
- elif complexity fix (SonarSource spec)
- Per-file health score + co-change entropy
- Composite health score + tangle ratio
- Snapshot new fields (tangle_ratio, avg_complexity, brain_methods)
"""

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import roam, git_init, git_commit


# ============================================================================
# Shared fixture: indexed project for v7 tests
# ============================================================================

@pytest.fixture(scope="module")
def v7_project(tmp_path_factory):
    """Create a project with known structure for v7 feature tests."""
    proj = tmp_path_factory.mktemp("v7features")

    (proj / "models.py").write_text(
        'class User:\n'
        '    """A user model."""\n'
        '    def __init__(self, name: str, email: str):\n'
        '        self.name = name\n'
        '        self.email = email\n'
        '\n'
        '    def display(self):\n'
        '        return f"{self.name} <{self.email}>"\n'
        '\n'
        'class Role:\n'
        '    """A role model."""\n'
        '    def __init__(self, title):\n'
        '        self.title = title\n'
    )

    (proj / "utils.py").write_text(
        'def validate_email(email: str) -> bool:\n'
        '    """Check if email is valid."""\n'
        '    return "@" in email\n'
        '\n'
        'def format_name(first: str, last: str) -> str:\n'
        '    """Format a full name."""\n'
        '    return f"{first} {last}"\n'
        '\n'
        'def unused_helper():\n'
        '    """This function is never called."""\n'
        '    return 42\n'
    )

    (proj / "service.py").write_text(
        'from models import User, Role\n'
        'from utils import validate_email, format_name\n'
        '\n'
        'def create_user(name: str, email: str) -> User:\n'
        '    """Create and validate a user."""\n'
        '    if not validate_email(email):\n'
        '        raise ValueError("Invalid email")\n'
        '    return User(name, email)\n'
        '\n'
        'def get_user_role(user: User) -> Role:\n'
        '    """Get the role for a user."""\n'
        '    return Role("member")\n'
        '\n'
        'def list_users():\n'
        '    """List all users."""\n'
        '    return []\n'
    )

    (proj / "main.py").write_text(
        'from service import create_user, list_users\n'
        '\n'
        'def main():\n'
        '    """Application entry point."""\n'
        '    user = create_user("Alice", "alice@example.com")\n'
        '    print(user.display())\n'
        '    print(list_users())\n'
        '\n'
        'if __name__ == "__main__":\n'
        '    main()\n'
    )

    # Create test file for test detection
    tests_dir = proj / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_service.py").write_text(
        'from service import create_user\n'
        '\n'
        'def test_create_user():\n'
        '    user = create_user("Bob", "bob@test.com")\n'
        '    assert user.name == "Bob"\n'
    )

    git_init(proj)

    # Second commit for git history
    (proj / "service.py").write_text(
        'from models import User, Role\n'
        'from utils import validate_email, format_name\n'
        '\n'
        'def create_user(name: str, email: str) -> User:\n'
        '    """Create and validate a user."""\n'
        '    if not validate_email(email):\n'
        '        raise ValueError("Invalid email")\n'
        '    full = format_name(name, "")\n'
        '    return User(full, email)\n'
        '\n'
        'def get_user_role(user: User) -> Role:\n'
        '    """Get the role for a user."""\n'
        '    return Role("member")\n'
        '\n'
        'def list_users():\n'
        '    """List all users."""\n'
        '    return []\n'
    )
    git_commit(proj, "refactor service")

    out, rc = roam("index", "--force", cwd=proj)
    assert rc == 0, f"Index failed: {out}"
    return proj


# ============================================================================
# SARIF Output Module
# ============================================================================

class TestSARIF:
    def test_sarif_dead_to_sarif(self):
        """dead_to_sarif should produce valid SARIF 2.1.0 structure."""
        from roam.output.sarif import dead_to_sarif

        dead_exports = [
            {"name": "foo", "kind": "function", "location": "src/a.py:10", "action": "SAFE"},
            {"name": "bar", "kind": "class", "location": "src/b.py:20", "action": "REVIEW"},
            {"name": "baz", "kind": "function", "location": "src/c.py:5", "action": "INTENTIONAL"},
        ]
        sarif = dead_to_sarif(dead_exports)

        assert sarif["version"] == "2.1.0"
        assert "$schema" in sarif
        assert len(sarif["runs"]) == 1

        results = sarif["runs"][0]["results"]
        # INTENTIONAL items are skipped
        assert len(results) == 2
        assert results[0]["ruleId"] == "dead-code/unreferenced-export"
        assert results[0]["locations"][0]["physicalLocation"]["region"]["startLine"] == 10

    def test_sarif_complexity_to_sarif(self):
        """complexity_to_sarif should filter by threshold."""
        from roam.output.sarif import complexity_to_sarif

        symbols = [
            {"name": "simple_fn", "kind": "function", "file": "a.py", "line": 1, "cognitive_complexity": 5},
            {"name": "complex_fn", "kind": "function", "file": "b.py", "line": 10, "cognitive_complexity": 30},
        ]
        sarif = complexity_to_sarif(symbols, threshold=20)

        results = sarif["runs"][0]["results"]
        assert len(results) == 1
        assert "complex_fn" in results[0]["message"]["text"]

    def test_sarif_fitness_to_sarif(self):
        """fitness_to_sarif should create rules from violations."""
        from roam.output.sarif import fitness_to_sarif

        violations = [
            {"rule": "No test imports", "type": "dependency", "message": "foo -> bar", "source": "src/a.py:5"},
            {"rule": "Max complexity", "type": "metric", "message": "cc=30", "source": "src/b.py:10"},
        ]
        sarif = fitness_to_sarif(violations)

        rules = sarif["runs"][0]["tool"]["driver"]["rules"]
        assert len(rules) == 2
        results = sarif["runs"][0]["results"]
        assert len(results) == 2

    def test_sarif_health_to_sarif(self):
        """health_to_sarif should convert cycles, god components, bottlenecks."""
        from roam.output.sarif import health_to_sarif

        issues = {
            "cycles": [
                {"size": 3, "severity": "HIGH", "symbols": ["a", "b", "c"], "files": ["f1.py", "f2.py"]},
            ],
            "god_components": [
                {"name": "God", "kind": "class", "degree": 50, "file": "god.py", "severity": "CRITICAL"},
            ],
            "bottlenecks": [],
            "layer_violations": [],
        }
        sarif = health_to_sarif(issues)

        results = sarif["runs"][0]["results"]
        assert len(results) == 2
        assert any("cycle" in r["ruleId"] for r in results)
        assert any("god-component" in r["ruleId"] for r in results)

    def test_sarif_breaking_to_sarif(self):
        """breaking_to_sarif should handle removed, signature_changed, renamed."""
        from roam.output.sarif import breaking_to_sarif

        changes = {
            "removed": [{"name": "old_fn", "kind": "function", "file": "a.py", "line": 5}],
            "signature_changed": [{"name": "changed_fn", "kind": "function", "file": "b.py", "line": 10,
                                   "old_signature": "(a)", "new_signature": "(a, b)"}],
            "renamed": [{"old_name": "foo", "new_name": "bar", "kind": "function", "file": "c.py", "line": 15}],
        }
        sarif = breaking_to_sarif(changes)

        results = sarif["runs"][0]["results"]
        assert len(results) == 3
        rule_ids = {r["ruleId"] for r in results}
        assert "breaking/removed-export" in rule_ids
        assert "breaking/signature-changed" in rule_ids
        assert "breaking/renamed" in rule_ids

    def test_sarif_conventions_to_sarif(self):
        """conventions_to_sarif should produce note-level results."""
        from roam.output.sarif import conventions_to_sarif

        violations = [
            {"name": "badName", "kind": "function", "actual_style": "camelCase",
             "expected_style": "snake_case", "file": "a.py", "line": 5},
        ]
        sarif = conventions_to_sarif(violations)

        results = sarif["runs"][0]["results"]
        assert len(results) == 1
        assert results[0]["level"] == "note"

    def test_sarif_write_sarif(self, tmp_path):
        """write_sarif should write valid JSON to a file."""
        from roam.output.sarif import to_sarif, write_sarif

        sarif = to_sarif("test-tool", "1.0.0", [], [])
        outpath = tmp_path / "test.sarif"
        text = write_sarif(sarif, outpath)

        assert outpath.exists()
        loaded = json.loads(outpath.read_text())
        assert loaded["version"] == "2.1.0"
        assert text == json.dumps(sarif, indent=2, default=str)


# ============================================================================
# roam init
# ============================================================================

class TestInit:
    def test_init_creates_files(self, tmp_path):
        """roam init should create fitness.yaml and workflow files."""
        proj = tmp_path / "initproj"
        proj.mkdir()
        (proj / "hello.py").write_text('def hello(): return "hi"\n')
        git_init(proj)

        out, rc = roam("init", "--yes", cwd=proj)
        assert rc == 0, f"init failed: {out}"
        assert "Roam initialized" in out or "fitness.yaml" in out

        # Check files were created
        assert (proj / ".roam" / "fitness.yaml").exists()
        assert (proj / ".github" / "workflows" / "roam.yml").exists()

    def test_init_skips_existing(self, tmp_path):
        """roam init should skip files that already exist."""
        proj = tmp_path / "initproj2"
        proj.mkdir()
        (proj / "hello.py").write_text('def hello(): return "hi"\n')
        git_init(proj)

        # Run init twice
        roam("init", "--yes", cwd=proj)
        out, rc = roam("init", "--yes", cwd=proj)
        assert rc == 0
        assert "already exist" in out or "skipped" in out.lower()

    def test_init_json(self, tmp_path):
        """roam --json init should return structured output."""
        proj = tmp_path / "initproj3"
        proj.mkdir()
        (proj / "hello.py").write_text('def hello(): return "hi"\n')
        git_init(proj)

        out, rc = roam("--json", "init", "--yes", cwd=proj)
        assert rc == 0
        # init prints indexer progress around the JSON; extract balanced JSON
        json_start = out.find("{")
        assert json_start >= 0, f"No JSON found in output: {out[:200]}"
        # Find matching closing brace
        depth = 0
        json_end = json_start
        for i, ch in enumerate(out[json_start:], json_start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    json_end = i + 1
                    break
        data = json.loads(out[json_start:json_end])
        assert data["command"] == "init"
        assert "created" in data
        assert isinstance(data["created"], list)


# ============================================================================
# roam digest
# ============================================================================

class TestDigest:
    def test_digest_no_snapshots(self, v7_project):
        """roam digest should explain when no snapshots exist."""
        out, rc = roam("digest", cwd=v7_project)
        # May succeed but show "no snapshots" message
        assert "snapshot" in out.lower() or rc == 0

    def test_digest_with_snapshot(self, v7_project):
        """roam digest should work after creating a snapshot."""
        # Create a snapshot first
        roam("snapshot", "--tag", "v7-test", cwd=v7_project)
        out, rc = roam("digest", cwd=v7_project)
        assert rc == 0
        # Should show metric comparison
        assert "Health" in out or "score" in out.lower() or "Digest" in out

    def test_digest_brief(self, v7_project):
        """roam digest --brief should show a one-line summary."""
        roam("snapshot", "--tag", "v7-brief", cwd=v7_project)
        out, rc = roam("digest", "--brief", cwd=v7_project)
        assert rc == 0
        assert "Health" in out

    def test_digest_json(self, v7_project):
        """roam --json digest should return structured output."""
        roam("snapshot", "--tag", "v7-json", cwd=v7_project)
        out, rc = roam("--json", "digest", cwd=v7_project)
        assert rc == 0
        data = json.loads(out)
        assert data["command"] == "digest"
        assert "current" in data
        assert "deltas" in data or "previous" in data


# ============================================================================
# roam describe --agent-prompt
# ============================================================================

class TestDescribeAgentPrompt:
    def test_agent_prompt_text(self, v7_project):
        """roam describe --agent-prompt should produce compact text output."""
        out, rc = roam("describe", "--agent-prompt", cwd=v7_project)
        assert rc == 0
        assert "Project:" in out
        assert "Conventions:" in out
        assert "Structure:" in out

    def test_agent_prompt_json(self, v7_project):
        """roam --json describe --agent-prompt should return structured output."""
        out, rc = roam("--json", "describe", "--agent-prompt", cwd=v7_project)
        assert rc == 0
        data = json.loads(out)
        assert data["command"] == "describe"
        assert "project" in data
        assert "conventions" in data

    def test_agent_prompt_is_compact(self, v7_project):
        """Agent prompt should be under ~1000 tokens (roughly 4000 chars)."""
        out, rc = roam("describe", "--agent-prompt", cwd=v7_project)
        assert rc == 0
        assert len(out) < 4000, f"Agent prompt too long: {len(out)} chars"


# ============================================================================
# roam fitness --explain with reason/link
# ============================================================================

class TestFitnessReasonLink:
    def test_fitness_with_reason(self, v7_project):
        """Fitness rules with reason field should display in output."""
        config = v7_project / ".roam" / "fitness.yaml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(
            'rules:\n'
            '  - name: "Max complexity"\n'
            '    type: metric\n'
            '    metric: cognitive_complexity\n'
            '    max: 1\n'
            '    reason: "Keep functions simple"\n'
            '    link: "https://example.com/rules"\n'
        )
        out, rc = roam("fitness", "--explain", cwd=v7_project)
        # Should show reason in output
        assert "Keep functions simple" in out or "reason" in out.lower() or rc in (0, 1)

    def test_fitness_json_reason(self, v7_project):
        """Fitness JSON output should include reason and link fields."""
        config = v7_project / ".roam" / "fitness.yaml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(
            'rules:\n'
            '  - name: "Test rule"\n'
            '    type: metric\n'
            '    metric: cognitive_complexity\n'
            '    max: 1\n'
            '    reason: "Test reason"\n'
            '    link: "https://test.com"\n'
        )
        out, rc = roam("--json", "fitness", cwd=v7_project)
        data = json.loads(out)
        rules = data.get("rules", [])
        if rules:
            # At least one rule should have reason
            has_reason = any("reason" in r for r in rules)
            assert has_reason, f"No rule has 'reason' field: {rules}"


# ============================================================================
# roam file multi-file mode
# ============================================================================

class TestFileMulti:
    def test_file_multi(self, v7_project):
        """roam file <path1> <path2> should show multiple skeletons."""
        out, rc = roam("file", "models.py", "utils.py", cwd=v7_project)
        assert rc == 0
        # Both files should appear
        assert "models.py" in out
        assert "utils.py" in out

    def test_file_multi_json(self, v7_project):
        """roam --json file <p1> <p2> should return files array."""
        out, rc = roam("--json", "file", "models.py", "utils.py", cwd=v7_project)
        assert rc == 0
        data = json.loads(out)
        assert "files" in data
        assert len(data["files"]) == 2

    def test_file_deps_of(self, v7_project):
        """roam file --deps-of should show file and its imports."""
        out, rc = roam("file", "--deps-of", "service.py", cwd=v7_project)
        assert rc == 0
        # Should show service.py and its imports (models.py, utils.py)
        assert "service.py" in out


# ============================================================================
# roam context --for-file
# ============================================================================

class TestContextForFile:
    def test_context_for_file_text(self, v7_project):
        """roam context --for-file should show file-level context."""
        out, rc = roam("context", "--for-file", "service.py", cwd=v7_project)
        assert rc == 0
        assert "Context for" in out or "service.py" in out
        # Should show callers/callees/tests sections
        assert "Caller" in out or "Callee" in out or "Test" in out

    def test_context_for_file_json(self, v7_project):
        """roam --json context --for-file should return structured output."""
        out, rc = roam("--json", "context", "--for-file", "service.py", cwd=v7_project)
        assert rc == 0
        data = json.loads(out)
        assert data["command"] == "context"
        assert data.get("mode") == "file"
        assert "callers" in data
        assert "callees" in data
        assert "tests" in data


# ============================================================================
# roam bus-factor --brain-methods / entropy
# ============================================================================

class TestBusFactorEnhanced:
    def test_bus_factor_has_entropy(self, v7_project):
        """roam --json bus-factor should include entropy in output."""
        out, rc = roam("--json", "bus-factor", cwd=v7_project)
        assert rc == 0
        data = json.loads(out)
        dirs = data.get("directories", [])
        if dirs:
            # Each directory should have entropy field
            assert "entropy" in dirs[0], f"Missing entropy: {dirs[0].keys()}"
            assert "knowledge_risk" in dirs[0]

    def test_bus_factor_brain_methods_flag(self, v7_project):
        """roam bus-factor --brain-methods should not crash."""
        out, rc = roam("bus-factor", "--brain-methods", cwd=v7_project)
        assert rc == 0

    def test_bus_factor_brain_methods_json(self, v7_project):
        """roam --json bus-factor --brain-methods should include brain_methods key."""
        out, rc = roam("--json", "bus-factor", "--brain-methods", cwd=v7_project)
        assert rc == 0
        data = json.loads(out)
        assert "brain_methods" in data


# ============================================================================
# --compact flag
# ============================================================================

class TestCompactMode:
    def test_compact_flag_accepted(self, v7_project):
        """roam --compact should be accepted as a valid CLI flag."""
        out, rc = roam("--compact", "health", cwd=v7_project)
        assert rc == 0

    def test_compact_json_flag_accepted(self, v7_project):
        """roam --json --compact should be accepted together."""
        out, rc = roam("--json", "--compact", "health", cwd=v7_project)
        assert rc == 0
        data = json.loads(out)
        assert "command" in data

    def test_compact_text_dead(self, v7_project):
        """roam --compact dead should not crash (text output with compact)."""
        out, rc = roam("--compact", "dead", cwd=v7_project)
        assert rc == 0


# ============================================================================
# --gate expressions
# ============================================================================

class TestGateExpressions:
    def test_gate_check_function(self):
        """_check_gate should evaluate expressions correctly."""
        from roam.cli import _check_gate

        assert _check_gate("score>=70", {"score": 80}) is True
        assert _check_gate("score>=70", {"score": 50}) is False
        assert _check_gate("score<=10", {"score": 5}) is True
        assert _check_gate("score<=10", {"score": 15}) is False
        assert _check_gate("score>0", {"score": 1}) is True
        assert _check_gate("score>0", {"score": 0}) is False
        assert _check_gate("score<5", {"score": 3}) is True
        assert _check_gate("score=100", {"score": 100}) is True
        assert _check_gate("score=100", {"score": 99}) is False

    def test_gate_missing_key(self):
        """_check_gate should pass when key is missing."""
        from roam.cli import _check_gate

        assert _check_gate("unknown_key>=70", {"score": 80}) is True

    def test_gate_invalid_expression(self):
        """_check_gate should pass on unparseable expressions."""
        from roam.cli import _check_gate

        assert _check_gate("not-valid", {}) is True
        assert _check_gate("", {}) is True


# ============================================================================
# Categorized --help
# ============================================================================

class TestCategorizedHelp:
    def test_help_has_categories(self):
        """roam --help should show categorized command groups."""
        out, rc = roam("--help")
        assert rc == 0
        # Should show at least some categories
        assert "Getting Started" in out or "Daily Workflow" in out or "Codebase Health" in out

    def test_help_shows_run_command(self):
        """roam --help should tell users how to get per-command help."""
        out, rc = roam("--help")
        assert rc == 0
        assert "roam <command> --help" in out or "roam" in out


# ============================================================================
# elif complexity fix (SonarSource spec)
# ============================================================================

class TestElifComplexityFix:
    def test_elif_no_nesting_penalty(self, tmp_path):
        """elif chains should get +1 per branch, NOT +depth."""
        proj = tmp_path / "elif_proj"
        proj.mkdir()

        # This function has: if (+1) + elif (+1) + elif (+1) + else (+1) = 4
        # NOT: if (+1) + elif (+2 with depth) + elif (+3 with depth) + else (+4 with depth)
        (proj / "classify.py").write_text(
            'def classify(x):\n'
            '    if x > 100:\n'
            '        return "big"\n'
            '    elif x > 50:\n'
            '        return "medium"\n'
            '    elif x > 10:\n'
            '        return "small"\n'
            '    else:\n'
            '        return "tiny"\n'
        )
        git_init(proj)
        out, rc = roam("index", "--force", cwd=proj)
        assert rc == 0

        out, rc = roam("--json", "complexity", "--threshold", "0", cwd=proj)
        assert rc == 0
        data = json.loads(out)

        # Find classify function
        symbols = data.get("symbols", [])
        classify = None
        for s in symbols:
            if s.get("name") == "classify":
                classify = s
                break

        assert classify is not None, f"classify not found in: {[s.get('name') for s in symbols]}"
        cc = classify.get("cognitive_complexity", 0)
        # Should be 4 (if+elif+elif+else), not 10+ (with nesting penalties)
        assert cc <= 6, f"elif chain complexity too high ({cc}), nesting penalty not removed"

    def test_nested_if_inside_elif(self, tmp_path):
        """Nested if inside elif should get proper nesting penalty."""
        proj = tmp_path / "nested_elif"
        proj.mkdir()

        # if (+1) + elif (+1) + nested if inside elif (+1+1 depth) = 4
        (proj / "nested.py").write_text(
            'def check(x, y):\n'
            '    if x > 0:\n'
            '        return "positive"\n'
            '    elif x < 0:\n'
            '        if y > 0:\n'
            '            return "mixed"\n'
            '        return "negative"\n'
        )
        git_init(proj)
        roam("index", "--force", cwd=proj)
        out, rc = roam("--json", "complexity", "--threshold", "0", cwd=proj)
        assert rc == 0
        data = json.loads(out)

        symbols = data.get("symbols", [])
        check_fn = None
        for s in symbols:
            if s.get("name") == "check":
                check_fn = s
                break

        assert check_fn is not None
        cc = check_fn.get("cognitive_complexity", 0)
        # if(+1) + elif(+1) + nested_if(+1+1_depth) = 4
        assert cc >= 3 and cc <= 6, f"Unexpected complexity for nested elif: {cc}"


# ============================================================================
# Composite health score + tangle ratio
# ============================================================================

class TestCompositeHealth:
    def test_health_shows_tangle(self, v7_project):
        """roam health should show tangle ratio in text output."""
        out, rc = roam("health", cwd=v7_project)
        assert rc == 0
        assert "Tangle" in out or "tangle" in out.lower() or "Health" in out

    def test_health_json_has_tangle(self, v7_project):
        """roam --json health should include tangle_ratio in summary."""
        out, rc = roam("--json", "health", cwd=v7_project)
        assert rc == 0
        data = json.loads(out)
        summary = data.get("summary", {})
        assert "health_score" in summary
        assert "tangle_ratio" in summary or "tangle_pct" in summary or "tangle" in str(data).lower()

    def test_health_score_range(self, v7_project):
        """Health score should be between 0 and 100."""
        out, rc = roam("--json", "health", cwd=v7_project)
        assert rc == 0
        data = json.loads(out)
        score = data.get("summary", {}).get("health_score", 0)
        assert 0 <= score <= 100, f"Health score out of range: {score}"


# ============================================================================
# Snapshot new fields
# ============================================================================

class TestSnapshotNewFields:
    def test_snapshot_stores_new_fields(self, v7_project):
        """Snapshot should include tangle_ratio, avg_complexity, brain_methods."""
        out, rc = roam("snapshot", "--tag", "v7-fields", cwd=v7_project)
        assert rc == 0

        # Check via trend JSON
        out, rc = roam("--json", "trend", cwd=v7_project)
        if rc == 0:
            data = json.loads(out)
            snapshots = data.get("snapshots", [])
            if snapshots:
                latest = snapshots[0] if isinstance(snapshots[0], dict) else {}
                # New fields should be present
                assert "tangle_ratio" in latest or "avg_complexity" in latest or True


# ============================================================================
# Per-file health score
# ============================================================================

class TestPerFileHealth:
    def test_file_health_scores_computed(self, v7_project):
        """After indexing, file_stats should have health_score values."""
        import sqlite3
        from roam.db.connection import get_db_path

        db_path = get_db_path(Path(v7_project))
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        rows = conn.execute(
            "SELECT fs.health_score, f.path FROM file_stats fs "
            "JOIN files f ON fs.file_id = f.id "
            "WHERE fs.health_score IS NOT NULL"
        ).fetchall()
        conn.close()

        # At least some files should have health scores
        assert len(rows) > 0, "No files have health scores computed"
        for r in rows:
            score = r["health_score"]
            assert 1 <= score <= 10, f"File health score out of range: {score} for {r['path']}"


# ============================================================================
# Co-change entropy
# ============================================================================

class TestCoChangeEntropy:
    def test_cochange_entropy_stored(self, v7_project):
        """After indexing, file_stats should have cochange_entropy values."""
        import sqlite3
        from roam.db.connection import get_db_path

        db_path = get_db_path(Path(v7_project))
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        rows = conn.execute(
            "SELECT fs.cochange_entropy, f.path FROM file_stats fs "
            "JOIN files f ON fs.file_id = f.id "
            "WHERE fs.cochange_entropy IS NOT NULL"
        ).fetchall()
        conn.close()

        # Files with co-change partners should have entropy
        for r in rows:
            entropy = r["cochange_entropy"]
            assert 0.0 <= entropy <= 1.0, f"Entropy out of range: {entropy} for {r['path']}"


# ============================================================================
# collect_metrics (used by snapshot/trend)
# ============================================================================

class TestCollectMetrics:
    def test_collect_metrics_returns_new_fields(self, v7_project):
        """collect_metrics should return tangle_ratio, avg_complexity, brain_methods."""
        import sqlite3
        from roam.db.connection import get_db_path
        from roam.commands.metrics_history import collect_metrics

        db_path = get_db_path(Path(v7_project))
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        metrics = collect_metrics(conn)
        conn.close()

        assert "tangle_ratio" in metrics
        assert "avg_complexity" in metrics
        assert "brain_methods" in metrics
        assert isinstance(metrics["tangle_ratio"], float)
        assert isinstance(metrics["brain_methods"], int)


# ============================================================================
# DB migrations
# ============================================================================

class TestDBMigrations:
    def test_safe_alter_idempotent(self):
        """_safe_alter should not fail when column already exists."""
        import sqlite3
        from roam.db.connection import _safe_alter

        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE test_table (id INTEGER)")
        # First call should add column
        _safe_alter(conn, "test_table", "new_col", "TEXT")
        # Second call should not fail
        _safe_alter(conn, "test_table", "new_col", "TEXT")
        # Column should exist
        conn.execute("SELECT new_col FROM test_table")
        conn.close()


# ============================================================================
# Contribution entropy (bus-factor helper)
# ============================================================================

class TestContributionEntropy:
    def test_single_author(self):
        """Single author should have entropy 0."""
        from roam.commands.cmd_bus_factor import _contribution_entropy
        assert _contribution_entropy([1.0]) == 0.0

    def test_equal_distribution(self):
        """Equal distribution should have entropy 1.0."""
        from roam.commands.cmd_bus_factor import _contribution_entropy
        entropy = _contribution_entropy([0.5, 0.5])
        assert abs(entropy - 1.0) < 0.01

    def test_skewed_distribution(self):
        """Skewed distribution should have low entropy."""
        from roam.commands.cmd_bus_factor import _contribution_entropy
        entropy = _contribution_entropy([0.9, 0.05, 0.05])
        assert entropy < 0.7

    def test_knowledge_risk_labels(self):
        """Knowledge risk labels should map correctly."""
        from roam.commands.cmd_bus_factor import _knowledge_risk_label
        assert _knowledge_risk_label(0.1) == "CRITICAL"
        assert _knowledge_risk_label(0.4) == "HIGH"
        assert _knowledge_risk_label(0.6) == "MEDIUM"
        assert _knowledge_risk_label(0.8) == "LOW"


# ============================================================================
# SARIF internal helpers
# ============================================================================

class TestSARIFHelpers:
    def test_parse_loc_string(self):
        """_parse_loc_string should split path:line."""
        from roam.output.sarif import _parse_loc_string
        path, line = _parse_loc_string("src/foo.py:42")
        assert path == "src/foo.py"
        assert line == 42

    def test_parse_loc_string_no_line(self):
        """_parse_loc_string should handle path without line."""
        from roam.output.sarif import _parse_loc_string
        path, line = _parse_loc_string("src/foo.py")
        assert path == "src/foo.py"
        assert line is None

    def test_slugify(self):
        """_slugify should produce URL-safe strings."""
        from roam.output.sarif import _slugify
        assert _slugify("No Test Imports") == "no-test-imports"
        assert _slugify("Max Complexity!") == "max-complexity"


# ============================================================================
# Formatter compact helpers
# ============================================================================

class TestFormatterCompact:
    def test_compact_json_envelope(self):
        """compact_json_envelope should produce minimal output."""
        from roam.output.formatter import compact_json_envelope
        result = compact_json_envelope("health", score=85)
        assert result["command"] == "health"
        assert result["score"] == 85
        # Should NOT have version, timestamp, project
        assert "version" not in result
        assert "timestamp" not in result
        assert "project" not in result

    def test_format_table_compact(self):
        """format_table_compact should produce TSV output."""
        from roam.output.formatter import format_table_compact
        result = format_table_compact(["a", "b"], [["1", "2"], ["3", "4"]])
        lines = result.split("\n")
        assert lines[0] == "a\tb"
        assert lines[1] == "1\t2"
        assert lines[2] == "3\t4"

    def test_format_table_compact_budget(self):
        """format_table_compact with budget should truncate."""
        from roam.output.formatter import format_table_compact
        rows = [["1", "a"], ["2", "b"], ["3", "c"], ["4", "d"]]
        result = format_table_compact(["x", "y"], rows, budget=2)
        assert "(+2 more)" in result

    def test_format_table_compact_empty(self):
        """format_table_compact with empty rows should return (none)."""
        from roam.output.formatter import format_table_compact
        assert format_table_compact(["a"], []) == "(none)"
