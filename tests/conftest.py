"""Shared test fixtures and helpers for roam tests.

Provides:
- Subprocess helper: roam() for smoke/E2E tests
- Git helpers: git_init(), git_commit()
- CliRunner fixtures: cli_runner, invoke_cli()
- Composable project fixtures: git_repo -> python_project -> indexed_project
- Factory fixture: project_factory for custom file combinations
- JSON validation helpers: parse_json_output(), assert_json_envelope()
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
from click.testing import CliRunner

# ===========================================================================
# Subprocess helpers (kept for backward compat + smoke tests)
# ===========================================================================


def roam(*args, cwd=None):
    """Run a roam CLI command and return (output, returncode)."""
    result = subprocess.run(
        [sys.executable, "-m", "roam"] + list(args),
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    return result.stdout + result.stderr, result.returncode


def git_init(path):
    """Initialize a git repo, add all files, and commit."""
    subprocess.run(["git", "init"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, capture_output=True)


def git_commit(path, msg="update"):
    """Stage all and commit."""
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True)
    subprocess.run(["git", "commit", "-m", msg], cwd=path, capture_output=True)


# ===========================================================================
# In-process index helper (faster than subprocess)
# ===========================================================================


def index_in_process(project_path, *extra_args):
    """Run `roam index` in-process via CliRunner (faster than subprocess).

    Args:
        project_path: Path to the project directory.
        *extra_args: Additional args (e.g. "--force", "--verbose").

    Returns (output, exit_code) like the subprocess roam() helper.
    """
    from roam.cli import cli

    runner = CliRunner()
    old_cwd = os.getcwd()
    try:
        os.chdir(str(project_path))
        result = runner.invoke(cli, ["index"] + list(extra_args), catch_exceptions=False)
    finally:
        os.chdir(old_cwd)
    return result.output, result.exit_code


# ===========================================================================
# CliRunner helpers
# ===========================================================================


# ===========================================================================
# Collection hook: skip dogfood-corpus-dependent tests when internal/dogfood
# is absent (gitignored private corpus per CLAUDE.md — not on CI / public clones)
# ===========================================================================


def pytest_collection_modifyitems(config, items):
    """Skip tests whose source file references ``internal/dogfood`` when
    that directory is absent on disk.

    The dogfood corpus is intentionally gitignored. Tests that depend on it
    pass on local dev (where Cranot has the dir) but fail on CI / public
    clones with FileNotFoundError or empty-corpus assertions. Rather than
    edit each of the 17 dogfood-dependent test files individually, do the
    skip centrally based on a source-text check.
    """
    import pathlib

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    dogfood_dir = repo_root / "internal" / "dogfood"
    if dogfood_dir.is_dir():
        # Local dev has the corpus — let tests run normally.
        return

    skip_marker = pytest.mark.skip(
        reason="internal/dogfood/ is gitignored — not available on CI / public clones",
    )
    _cache: dict[pathlib.Path, bool] = {}
    for item in items:
        src = pathlib.Path(item.fspath)
        if src not in _cache:
            try:
                text = src.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                _cache[src] = False
                continue
            _cache[src] = ("internal/dogfood" in text) or ("internal\\dogfood" in text)
        if _cache[src]:
            item.add_marker(skip_marker)


@pytest.fixture(autouse=True)
def _clear_graph_cache_between_tests():
    """Pass 69 introduced a process-wide graph cache keyed on
    ``id(conn)``. When tests share a Python process, ``id`` reuse can
    return a stale graph from a previously-closed connection. Clear the
    cache between every test so partition / orchestrate tests don't
    leak state into each other.
    """
    try:
        from roam.graph.builder import clear_graph_cache

        clear_graph_cache()
    except Exception:
        pass
    yield
    try:
        from roam.graph.builder import clear_graph_cache

        clear_graph_cache()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _disable_shallow_git_history_for_marked_tests(request, monkeypatch):
    """W984: tests marked ``@pytest.mark.git_history`` get ``ROAM_GIT_SINCE=0``.

    The W405 ``_DEFAULT_SINCE = "365d"`` in ``src/roam/index/git_stats.py``
    silently drops fixture commits dated more than 1 year ago, which was the
    W978 bug on ``test_bus_factor_stale_kind_emitted``: the fixture backdates
    a commit to 2024-01-01 to exercise the ``stale-ownership`` kind, but the
    first-index ``git log --since=365d`` window dropped it, yielding zero git
    stats and zero findings.

    Mark-gated rather than unconditional: W405 is the correct default for
    fresh indexes on real repos; tests that explicitly want to verify
    shallow-history behaviour (e.g. ``test_very_short_window_no_commits`` in
    ``test_dev_profile.py``) MUST leave the mark off. Tests that backdate
    fixture commits past 365 days from now opt IN by adding the mark.
    """
    if request.node.get_closest_marker("git_history"):
        monkeypatch.setenv("ROAM_GIT_SINCE", "0")


@pytest.fixture
def cli_runner():
    """Provide a Click CliRunner for in-process CLI testing."""
    return CliRunner()


def invoke_cli(runner, args, cwd=None, json_mode=False):
    """Invoke the roam CLI via CliRunner.

    Args:
        runner: CliRunner instance
        args: list of CLI arguments (e.g. ["health"])
        cwd: directory to run in (applied via os.chdir, restored in finally)
        json_mode: if True, prepend --json flag
    Returns:
        click.testing.Result
    """
    from roam.cli import cli

    full_args = []
    if json_mode:
        full_args.append("--json")
    full_args.extend(args)

    old_cwd = os.getcwd()
    try:
        if cwd:
            os.chdir(str(cwd))
        result = runner.invoke(cli, full_args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)

    return result


# ===========================================================================
# PyYAML-missing fixture (W1027 — extracted from W1018 + W1019 fan-out)
# ===========================================================================


@pytest.fixture
def no_pyyaml(monkeypatch):
    """Simulate a PyYAML-missing environment for tiny-parser fallback tests.

    Originally copy-pasted across 6 test files after W1018 / W1019 / W1051 /
    W1052 each landed a no-PyYAML branch test. W1027 hoisted the canonical
    shape (intercept-at-builtins + pop from ``sys.modules``) into this
    fixture. The strictest variant wins: ``__import__`` interception blocks
    fresh imports inside the helper, and ``sys.modules`` pop drops any cached
    top-level ``yaml`` so the helper truly re-imports.

    Usage::

        def test_helper_falls_back_without_pyyaml(tmp_path, no_pyyaml):
            ...
    """
    import builtins
    import sys

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "yaml" or name.startswith("yaml."):
            raise ImportError("W1027 fixture: PyYAML unavailable for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    monkeypatch.delitem(sys.modules, "yaml", raising=False)
    yield
    # monkeypatch auto-restores the patched __import__ and sys.modules entry.


# ===========================================================================
# JSON validation helpers
# ===========================================================================


def parse_json_output(result, command=None):
    """Parse JSON from a CliRunner result.

    Args:
        result: click.testing.Result from invoke_cli
        command: optional command name for better error messages
    Returns:
        Parsed dict from JSON output
    Raises:
        AssertionError with context on parse failure
    """
    assert result.exit_code == 0, f"Command {command or '?'} failed (exit {result.exit_code}):\n{result.output}"
    # Click 8.3 always separates stdout/stderr — prefer result.stdout when
    # available so a deprecation note (or any other err=True output) on
    # stderr doesn't contaminate the JSON we're trying to parse. Older Click
    # didn't expose stdout independently; fall back to result.output then.
    raw = getattr(result, "stdout", None)
    if raw is None:
        raw = result.output
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        pytest.fail(f"Invalid JSON from {command or '?'}: {e}\nOutput was:\n{raw[:500]}")


def assert_json_envelope(data, command=None):
    """Validate that a parsed JSON dict follows the roam envelope contract.

    Checks required top-level keys: command, version, summary.
    Checks _meta contains timestamp (non-deterministic metadata).
    Checks summary contains a verdict string.
    """
    assert isinstance(data, dict), f"Expected dict, got {type(data)}"
    assert "command" in data, "Missing 'command' key in envelope"
    assert "version" in data, "Missing 'version' key in envelope"
    assert "summary" in data, "Missing 'summary' key in envelope"
    # timestamp lives in _meta (or legacy top-level for backward compat)
    meta = data.get("_meta", {})
    assert "timestamp" in meta or "timestamp" in data, "Missing 'timestamp' in _meta or top-level envelope"
    if command:
        assert data["command"] == command, f"Expected command={command}, got {data['command']}"
    summary = data["summary"]
    assert isinstance(summary, dict), f"summary should be dict, got {type(summary)}"


# ===========================================================================
# Composable project fixtures
# ===========================================================================


@pytest.fixture
def indexed_project(tmp_path, monkeypatch):
    """Indexed Python project: empty git repo + 3-file Python source tree
    + ``roam index`` run. Returns the project directory path.

    Replaces the prior 3-fixture chain (``git_repo`` -> ``python_project``
    -> ``indexed_project``) that had zero direct consumers on the
    upstream two fixtures. Collapsed 2026-05-23 per the conftest-cleanup
    audit; helpers ``git_init`` / ``git_commit`` / ``index_in_process``
    stay module-level since they have other callers (project_factory,
    project_with_snapshots, make_src_project).

    W414c-BAIL (2026-05-17, load-bearing): function-scope is intentional.
    Promoting to module-scope via ``tmp_path_factory`` would break:

      - test_findings_pr_risk.py asserts ``count == 0`` for the pr-risk
        detector after a ``--persist`` run earlier in the module; it also
        ``DROP TABLE findings`` in one branch which would leak schema state.
      - test_findings_doctor.py: same ``count == 0`` + DROP TABLE
        pattern for the doctor detector.
      - test_findings_critique.py asserts ``count == 0`` for critique.
      - test_grep_extended.py adds ``patterns.txt`` at the repo root and
        ``src/with_dot.py`` without restoring them, so module-scope reuse
        would leak leaked files into subsequent grep/list/index
        assertions.

    Per-file overrides (17 files already do this via ``tmp_path_factory``)
    remain the canonical optimisation. Do NOT re-attempt centralised
    promotion without first decoupling the findings-registry tests
    (e.g. autouse ``DELETE FROM findings`` fixture in each findings-*
    module) AND auditing every transitive mutator for cleanup.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(".roam/\n")
    git_init(repo)

    src = repo / "src"
    src.mkdir()

    (src / "models.py").write_text(
        "class User:\n"
        '    """A user model."""\n'
        "    def __init__(self, name, email):\n"
        "        self.name = name\n"
        "        self.email = email\n"
        "\n"
        "    def display_name(self):\n"
        "        return self.name.title()\n"
        "\n"
        "    def validate_email(self):\n"
        '        return "@" in self.email\n'
        "\n"
        "\n"
        "class Admin(User):\n"
        '    """An admin user."""\n'
        '    def __init__(self, name, email, role="admin"):\n'
        "        super().__init__(name, email)\n"
        "        self.role = role\n"
        "\n"
        "    def promote(self, user):\n"
        "        pass\n"
    )

    (src / "service.py").write_text(
        "from models import User, Admin\n"
        "\n"
        "\n"
        "def create_user(name, email):\n"
        '    """Create a new user."""\n'
        "    user = User(name, email)\n"
        "    if not user.validate_email():\n"
        '        raise ValueError("Invalid email")\n'
        "    return user\n"
        "\n"
        "\n"
        "def get_display(user):\n"
        '    """Get display name."""\n'
        "    return user.display_name()\n"
        "\n"
        "\n"
        "def unused_helper():\n"
        '    """This function is never called (dead code)."""\n'
        "    return 42\n"
    )

    (src / "utils.py").write_text(
        "def format_name(first, last):\n"
        '    """Format a full name."""\n'
        '    return f"{first} {last}"\n'
        "\n"
        "\n"
        "def parse_email(raw):\n"
        '    """Parse an email address."""\n'
        '    if "@" not in raw:\n'
        "        return None\n"
        '    parts = raw.split("@")\n'
        '    return {"user": parts[0], "domain": parts[1]}\n'
        "\n"
        "\n"
        'UNUSED_CONSTANT = "never_referenced"\n'
    )

    git_commit(repo, "add python project")

    monkeypatch.chdir(repo)
    out, rc = index_in_process(repo)
    assert rc == 0, f"roam index failed:\n{out}"
    return repo


def make_src_project(tmp_path, files, *, src_dir="src"):
    """Create a git-initialised project with files under ``src/`` and commit.

    Replaces the ``_make_project`` helpers duplicated across
    ``test_clones``, ``test_retrieve``, ``test_retrieve_seeds``. Use this
    for the v12 test suites that need a per-test custom file layout.

    The project is **not** indexed — call ``roam index`` (typically via
    ``CliRunner``) to populate the DB, since several test classes
    deliberately interleave index/clones/retrieve calls.

    Parameters
    ----------
    tmp_path:
        The pytest ``tmp_path`` fixture.
    files:
        Mapping of ``{relative_path_under_src: content}``. ``content``
        is dedented before write.
    src_dir:
        Directory name under the project root (default ``src``).

    Returns
    -------
    pathlib.Path
        The project root directory.
    """
    import textwrap

    proj = tmp_path / "proj"
    proj.mkdir()
    src = proj / src_dir
    src.mkdir()
    # Match production conventions — users gitignore the index directory.
    # Without this, indexer output marks the working tree as dirty in tests.
    (proj / ".gitignore").write_text(".roam/\n", encoding="utf-8")
    for name, content in files.items():
        p = src / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(content), encoding="utf-8")
    subprocess.run(["git", "init"], cwd=str(proj), capture_output=True)
    subprocess.run(["git", "add", "."], cwd=str(proj), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init", "--allow-empty"],
        cwd=str(proj),
        capture_output=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    )
    return proj


@pytest.fixture
def project_factory(tmp_path_factory):
    """Factory fixture for creating custom project layouts.

    Usage:
        def test_something(project_factory):
            proj = project_factory({
                "app.py": 'def main(): pass',
                "lib/helper.py": 'def help(): pass',
            })
            # proj is an indexed project path

    Returns a callable that accepts a dict of {relative_path: content}
    and returns an indexed project path.
    """

    def _create(files, *, index=True, extra_commits=None):
        proj = tmp_path_factory.mktemp("project")
        (proj / ".gitignore").write_text(".roam/\n")

        for rel_path, content in files.items():
            fp = proj / rel_path
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content)

        git_init(proj)

        if extra_commits:
            for commit_files, msg in extra_commits:
                for rel_path, content in commit_files.items():
                    fp = proj / rel_path
                    fp.parent.mkdir(parents=True, exist_ok=True)
                    fp.write_text(content)
                git_commit(proj, msg)

        if index:
            out, rc = index_in_process(proj)
            assert rc == 0, f"roam index failed:\n{out}"

        return proj

    return _create


# ===========================================================================
# Snapshot helper for trend tests
# ===========================================================================


@pytest.fixture
def project_with_snapshots(indexed_project, monkeypatch):
    """An indexed project with multiple snapshots for trend testing.

    Creates 5 snapshots by modifying files between each.
    Returns the project path.
    """
    monkeypatch.chdir(indexed_project)

    # Snapshot 1 is created by index. Create 4 more.
    src = indexed_project / "src"
    for i in range(2, 6):
        (src / f"extra_{i}.py").write_text(f'def func_{i}():\n    """Function {i}."""\n    return {i}\n')
        git_commit(indexed_project, f"add extra_{i}")
        out, rc = index_in_process(indexed_project)
        assert rc == 0, f"roam index (snapshot {i}) failed:\n{out}"
        # trends --save creates a snapshot; don't assert exit code
        roam("trends", "--save", "--tag", f"v{i}", cwd=indexed_project)

    return indexed_project
