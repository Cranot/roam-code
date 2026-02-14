# Idea A: Superior Test Suite (11 New Test Files)

## Origin
chuckjewell fork -- 11 subprocess-based test files (+1,046 lines) that we lack entirely.

## Why
We have 12 test files / 669 tests. The fork identified 11 untested feature areas:
context batch, coupling --against, coverage-gaps, dead grouping, diff parity,
grep --source-only, JSON contracts, report config, report presets, risk --explain,
snapshot/trend. ALL features exist in our code but have ZERO dedicated tests.

## Our Approach (Deeper, Cleaner, Smarter)

Fork used subprocess-based tests with duplicated helpers. We will use:

### Architecture
- **CliRunner** (in-process) for ~90% of tests -- 10-50ms vs 1-2s per test
- **Subprocess** only for smoke tests (entry point, encoding, exit codes)
- **Shared conftest.py** with composable fixtures (no duplication)
- **JSON contract validation** with jsonschema + parametric sweep
- **Snapshot testing** with syrupy for output format regression
- **Property-based testing** with Hypothesis for robustness

### conftest.py Fixture Layers
1. `git_repo(tmp_path)` -- empty git repo with initial commit
2. `python_project(git_repo)` -- adds source files
3. `indexed_project(python_project)` -- runs `roam index`
4. `project_factory(tmp_path_factory)` -- factory for custom file combos

### Test File Plan (12 files, ~545 tests estimated)

| File | Scope | Est. Tests | Covers |
|------|-------|-----------|--------|
| conftest.py | -- | 0 | Shared fixtures, JSON validators, helpers |
| test_smoke.py | E2E | ~15 | Entry point, encoding, exit codes (subprocess) |
| test_index.py | Integration | ~40 | Full/incremental indexing, language detection |
| test_commands_exploration.py | Integration | ~80 | search, grep, file, symbol, trace, deps, fan, impact |
| test_commands_health.py | Integration | ~60 | health, weather, debt, complexity, alerts, trend, fitness, snapshot |
| test_commands_architecture.py | Integration | ~50 | map, layers, clusters, coupling, entry-points, patterns |
| test_commands_workflow.py | Integration | ~50 | preflight, pr-risk, diff, context, affected-tests, diagnose |
| test_commands_refactoring.py | Integration | ~40 | dead, safe-delete, split, conventions, breaking |
| test_json_contracts.py | Contract | ~60 | Parametric envelope tests for ALL --json commands |
| test_languages.py | Integration | ~80 | Per-language extraction with project_factory |
| test_formatters.py | Unit | ~40 | Pure function tests for formatter.py |
| test_properties.py | Property | ~30 | Hypothesis fuzz tests for search, formatters, batched_in |

### Key Patterns

```python
# JSON contract validation (parametric across all commands)
COMMANDS_WITH_JSON = ["health", "map", "dead", "weather", "clusters", ...]

@pytest.mark.parametrize("cmd", COMMANDS_WITH_JSON)
def test_json_envelope_contract(indexed_project, monkeypatch, cmd):
    monkeypatch.chdir(indexed_project)
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", cmd], catch_exceptions=False)
    data = parse_json_output(result, cmd)
    assert isinstance(data["summary"].get("verdict"), str)
```

### New Dependencies (dev only)
- jsonschema >= 4.0
- syrupy >= 4.0
- pytest-timeout >= 2.0
- hypothesis (optional, for property tests)

## Priority: HIGH -- Critical coverage gap
## Effort: Medium (2-3 sessions)
## Files touched: tests/ only (no production code changes)
