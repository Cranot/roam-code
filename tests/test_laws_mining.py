"""Tests for R27 — ``roam laws mine`` / ``roam laws check``.

Covers:
  - Naming-law mining + checking (Strategy A)
  - Import-layering law mining + checking (Strategy B)
  - Testing-law mining + checking (Strategy C)
  - YAML round-trip
  - ``--strict`` exit-5 behaviour
  - ``laws explain`` returning the evidence dict
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import (  # noqa: E402
    assert_json_envelope,
    git_commit,
    git_init,
    index_in_process,
    invoke_cli,
    parse_json_output,
)

from roam.cli import cli  # noqa: E402
from roam.db.connection import open_db  # noqa: E402
from roam.laws.checker import check_laws, parse_added  # noqa: E402
from roam.laws.miner import Law, mine_laws  # noqa: E402
from roam.laws.serializer import dump_laws_yaml, load_laws_yaml  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _index(proj: Path) -> None:
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed: {out}"


@pytest.fixture
def snake_project(tmp_path, monkeypatch):
    """9 snake_case + 1 camelCase function. Used by naming-law tests."""
    proj = tmp_path / "snakeproj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    body = textwrap.dedent(
        """\
        def fetch_user(id): return id
        def update_user(id): return id
        def delete_user(id): return id
        def list_users(): return []
        def make_token(): return "t"
        def parse_email(raw): return raw
        def format_name(first, last): return first
        def validate_input(x): return x
        def serialize_payload(p): return p
        def myCamelOdd(x): return x
        """
    )
    (proj / "app.py").write_text(body)
    git_init(proj)
    monkeypatch.chdir(proj)
    _index(proj)
    return proj


@pytest.fixture
def mixed_project(tmp_path, monkeypatch):
    """5 snake_case + 5 camelCase — below the conformance threshold."""
    proj = tmp_path / "mixedproj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    body = textwrap.dedent(
        """\
        def fetch_user(id): return id
        def update_user(id): return id
        def delete_user(id): return id
        def list_users(): return []
        def make_token(): return "t"
        def fetchUser(id): return id
        def updateUser(id): return id
        def deleteUser(id): return id
        def listUsers(): return []
        def makeToken(): return "t"
        """
    )
    (proj / "app.py").write_text(body)
    git_init(proj)
    monkeypatch.chdir(proj)
    _index(proj)
    return proj


@pytest.fixture
def layered_project(tmp_path, monkeypatch):
    """A multi-directory project where ``handlers`` consistently imports
    from ``db`` — used by import-layering tests.
    """
    proj = tmp_path / "layered"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    (src / "__init__.py").write_text("")

    handlers = src / "handlers"
    handlers.mkdir()
    (handlers / "__init__.py").write_text("")
    db_dir = src / "db"
    db_dir.mkdir()
    (db_dir / "__init__.py").write_text("")

    # Create several db modules
    db_names = ("users", "orders", "payments", "tokens", "sessions", "audit")
    for name in db_names:
        (db_dir / f"{name}.py").write_text(
            f"def get_{name}(): return []\ndef put_{name}(x): return x\n"
        )

    # Each handler imports from src.db.* — strong A -> B signal. Use the
    # same set so every handler resolves to a real db module (the indexer
    # drops unresolved imports from file_edges).
    for name in db_names:
        (handlers / f"{name}.py").write_text(
            f"from src.db.{name} import get_{name}, put_{name}\n"
            f"def handle_{name}():\n    return get_{name}()\n"
        )

    git_init(proj)
    monkeypatch.chdir(proj)
    _index(proj)
    return proj


@pytest.fixture
def tested_project(tmp_path, monkeypatch):
    """Five public functions, each with a matching ``test_*`` file."""
    proj = tmp_path / "tested"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    src = proj / "src"
    src.mkdir()
    tests = proj / "tests"
    tests.mkdir()

    public_fns = ["fetch_user", "update_user", "delete_user", "list_users", "make_token"]
    for fn in public_fns:
        (src / f"{fn}.py").write_text(f"def {fn}(*a, **k): return None\n")
        (tests / f"test_{fn}.py").write_text(
            f"from src.{fn} import {fn}\ndef test_{fn}(): {fn}()\n"
        )

    git_init(proj)
    monkeypatch.chdir(proj)
    _index(proj)
    return proj


# ---------------------------------------------------------------------------
# Strategy A — naming
# ---------------------------------------------------------------------------


def test_mine_emits_naming_law_when_90pct_conformance(snake_project):
    with open_db(readonly=True) as conn:
        laws = mine_laws(conn)

    naming = [law for law in laws if law.kind == "naming" and law.rule.get("symbol_kind") == "function"]
    assert naming, f"expected a naming law for function/snake_case, got: {[law.id for law in laws]}"
    law = naming[0]
    assert law.rule["style"] == "snake_case"
    assert law.evidence["conformance_pct"] >= 90
    assert law.confidence == "high"
    assert "examples" in law.evidence


def test_mine_skips_below_threshold(mixed_project):
    with open_db(readonly=True) as conn:
        laws = mine_laws(conn)

    # 50/50 split — no naming law for function should emit at the default
    # 70% threshold.
    naming_fn = [
        law for law in laws
        if law.kind == "naming" and law.rule.get("symbol_kind") == "function"
    ]
    assert not naming_fn, (
        f"expected no function naming law on 50/50 split, got: {naming_fn!r}"
    )


def test_check_naming_law_flags_violation():
    law = Law(
        id="snake_case_functions",
        kind="naming",
        description="Functions must be snake_case",
        evidence={"sample_size": 10, "conformance_pct": 95, "style": "snake_case"},
        severity="advisory",
        confidence="high",
        rule={"kind": "naming", "symbol_kind": "function", "style": "snake_case"},
    )
    diff = textwrap.dedent(
        """\
        diff --git a/app.py b/app.py
        --- a/app.py
        +++ b/app.py
        @@ -1,1 +1,3 @@
         existing = 1
        +def myCamelFunction(x):
        +    return x
        """
    )
    violations = check_laws([law], diff=diff)
    assert len(violations) == 1
    v = violations[0]
    assert v.law_id == "snake_case_functions"
    assert v.kind == "naming"
    assert "myCamelFunction" in v.message
    assert v.evidence["actual_style"] == "camelCase"
    assert v.evidence["expected_style"] == "snake_case"


def test_check_naming_law_clean_diff_passes():
    law = Law(
        id="snake_case_functions",
        kind="naming",
        description="Functions must be snake_case",
        evidence={"sample_size": 10, "conformance_pct": 95, "style": "snake_case"},
        confidence="high",
        rule={"kind": "naming", "symbol_kind": "function", "style": "snake_case"},
    )
    diff = textwrap.dedent(
        """\
        diff --git a/app.py b/app.py
        --- a/app.py
        +++ b/app.py
        @@ -1,1 +1,3 @@
         existing = 1
        +def my_snake_function(x):
        +    return x
        """
    )
    violations = check_laws([law], diff=diff)
    assert violations == []


# ---------------------------------------------------------------------------
# Strategy B — import layering
# ---------------------------------------------------------------------------


def test_mine_import_layering(layered_project):
    with open_db(readonly=True) as conn:
        laws = mine_laws(conn)

    import_laws = [law for law in laws if law.kind == "import"]
    assert import_laws, f"expected at least one import law, got: {[law.id for law in laws]}"

    # Confirm at least one law points handlers -> db
    handler_law = next(
        (law for law in import_laws
         if law.rule.get("from_dir", "").endswith("handlers")
         and law.rule.get("to_dir", "").endswith("db")),
        None,
    )
    assert handler_law is not None, (
        f"expected a handlers -> db law, got: "
        f"{[(law.rule.get('from_dir'), law.rule.get('to_dir')) for law in import_laws]}"
    )
    assert handler_law.evidence["conformance_pct"] >= 90


def test_check_import_violation():
    law = Law(
        id="imports_src_handlers_to_src_db",
        kind="import",
        description="Files in src/handlers/ import from src/db/",
        evidence={"sample_size": 10, "conformance_pct": 100, "from_dir": "src/handlers", "to_dir": "src/db"},
        confidence="high",
        rule={"kind": "import", "from_dir": "src/handlers", "to_dir": "src/db"},
    )
    diff = textwrap.dedent(
        """\
        diff --git a/src/handlers/sneaky.py b/src/handlers/sneaky.py
        new file mode 100644
        --- /dev/null
        +++ b/src/handlers/sneaky.py
        @@ -0,0 +1,2 @@
        +from src.forbidden.module import bad_helper
        +def handle_sneaky(): return bad_helper()
        """
    )
    violations = check_laws([law], diff=diff)
    assert len(violations) >= 1
    v = violations[0]
    assert v.law_id == "imports_src_handlers_to_src_db"
    assert v.kind == "import"
    assert "src/forbidden" in v.message or "forbidden" in v.message


# ---------------------------------------------------------------------------
# Strategy C — testing
# ---------------------------------------------------------------------------


def test_mine_testing_law(tested_project):
    with open_db(readonly=True) as conn:
        laws = mine_laws(conn)

    testing_laws = [law for law in laws if law.kind == "testing"]
    assert testing_laws, f"expected a testing law, got: {[law.id for law in laws]}"
    law = testing_laws[0]
    assert law.rule["kind"] == "testing"
    assert law.evidence["conformance_pct"] >= 70


def test_check_testing_law_flags_missing_test():
    law = Law(
        id="public_functions_must_be_tested",
        kind="testing",
        description="Public functions should have a matching test file",
        evidence={"sample_size": 5, "conformance_pct": 100},
        confidence="high",
        rule={"kind": "testing", "symbol_kind": "function", "test_pattern": "test_*"},
    )
    diff = textwrap.dedent(
        """\
        diff --git a/src/new_module.py b/src/new_module.py
        new file mode 100644
        --- /dev/null
        +++ b/src/new_module.py
        @@ -0,0 +1,2 @@
        +def brand_new_function(x):
        +    return x
        """
    )
    violations = check_laws([law], diff=diff)
    assert len(violations) == 1
    v = violations[0]
    assert v.law_id == "public_functions_must_be_tested"
    assert "brand_new_function" in v.message


def test_check_testing_law_passes_when_test_is_in_diff():
    law = Law(
        id="public_functions_must_be_tested",
        kind="testing",
        description="Public functions should have a matching test file",
        evidence={"sample_size": 5, "conformance_pct": 100},
        confidence="high",
        rule={"kind": "testing", "symbol_kind": "function", "test_pattern": "test_*"},
    )
    # Diff that adds both the symbol AND a matching test file.
    diff = textwrap.dedent(
        """\
        diff --git a/src/new_module.py b/src/new_module.py
        new file mode 100644
        --- /dev/null
        +++ b/src/new_module.py
        @@ -0,0 +1,2 @@
        +def brand_new_function(x):
        +    return x
        diff --git a/tests/test_brand_new_function.py b/tests/test_brand_new_function.py
        new file mode 100644
        --- /dev/null
        +++ b/tests/test_brand_new_function.py
        @@ -0,0 +1,2 @@
        +def test_brand_new_function():
        +    pass
        """
    )
    violations = check_laws([law], diff=diff)
    assert violations == [], f"expected no violations, got: {violations}"


# ---------------------------------------------------------------------------
# YAML round-trip
# ---------------------------------------------------------------------------


def test_laws_yml_round_trip(tmp_path):
    laws = [
        Law(
            id="snake_case_functions",
            kind="naming",
            description="Functions must be snake_case",
            evidence={"sample_size": 10, "conformance_pct": 100, "style": "snake_case"},
            severity="advisory",
            confidence="high",
            rule={"kind": "naming", "symbol_kind": "function", "style": "snake_case"},
        ),
        Law(
            id="imports_src_handlers_to_src_db",
            kind="import",
            description="Files in src/handlers/ import from src/db/",
            evidence={"sample_size": 8, "conformance_pct": 100, "from_dir": "src/handlers", "to_dir": "src/db"},
            severity="advisory",
            confidence="high",
            rule={"kind": "import", "from_dir": "src/handlers", "to_dir": "src/db"},
        ),
    ]
    text = dump_laws_yaml(laws)
    loaded = load_laws_yaml(text)
    assert len(loaded) == len(laws)
    for original, restored in zip(laws, loaded):
        assert restored.id == original.id
        assert restored.kind == original.kind
        assert restored.description == original.description
        assert restored.rule == original.rule
        # Run the *restored* law against a flagging diff and confirm same behavior
    diff = textwrap.dedent(
        """\
        diff --git a/app.py b/app.py
        --- a/app.py
        +++ b/app.py
        @@ -1,1 +1,2 @@
         existing = 1
        +def myBadName(): return 1
        """
    )
    v_before = check_laws(laws, diff=diff)
    v_after = check_laws(loaded, diff=diff)
    assert [v.law_id for v in v_before] == [v.law_id for v in v_after]


# ---------------------------------------------------------------------------
# CLI: --strict exit code & explain
# ---------------------------------------------------------------------------


def test_check_strict_exits_5(snake_project, cli_runner, monkeypatch):
    monkeypatch.chdir(snake_project)
    # Write a laws file with a strict blocker so --strict can trigger.
    laws_path = snake_project / "roam-laws.yml"
    laws_path.write_text(
        dump_laws_yaml(
            [
                Law(
                    id="snake_case_functions",
                    kind="naming",
                    description="Functions must be snake_case",
                    evidence={"sample_size": 10, "conformance_pct": 100, "style": "snake_case"},
                    severity="blocker",
                    confidence="high",
                    rule={
                        "kind": "naming",
                        "symbol_kind": "function",
                        "style": "snake_case",
                    },
                )
            ]
        )
    )

    # Add a violating function to working tree
    app = snake_project / "app.py"
    body = app.read_text()
    app.write_text(body + "\ndef anotherCamelFn(x):\n    return x\n")

    # Use --diff-file so the test is hermetic on Windows git environments.
    diff_path = snake_project / "_diff.patch"
    diff_path.write_text(
        textwrap.dedent(
            """\
            diff --git a/app.py b/app.py
            --- a/app.py
            +++ b/app.py
            @@ -10,0 +11,2 @@
            +def anotherCamelFn(x):
            +    return x
            """
        )
    )

    result = cli_runner.invoke(
        cli,
        [
            "laws",
            "check",
            "--laws-file",
            str(laws_path),
            "--diff-file",
            str(diff_path),
            "--strict",
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 5, f"expected exit 5, got {result.exit_code}\n{result.output}"
    assert "anotherCamelFn" in result.output


def test_explain_returns_evidence(snake_project, cli_runner, monkeypatch):
    monkeypatch.chdir(snake_project)
    laws_path = snake_project / "roam-laws.yml"
    laws_path.write_text(
        dump_laws_yaml(
            [
                Law(
                    id="snake_case_functions",
                    kind="naming",
                    description="Functions must be snake_case",
                    evidence={
                        "sample_size": 10,
                        "conformance_pct": 90,
                        "style": "snake_case",
                        "examples": ["fetch_user", "update_user"],
                    },
                    severity="advisory",
                    confidence="high",
                    rule={"kind": "naming", "symbol_kind": "function", "style": "snake_case"},
                )
            ]
        )
    )
    result = cli_runner.invoke(
        cli,
        ["--json", "laws", "explain", "snake_case_functions", "--laws-file", str(laws_path)],
        catch_exceptions=False,
    )
    data = parse_json_output(result, command="laws-explain")
    assert_json_envelope(data, command="laws-explain")
    assert data["law"]["id"] == "snake_case_functions"
    assert data["law"]["evidence"]["sample_size"] == 10
    assert data["law"]["evidence"]["conformance_pct"] == 90
    assert "fetch_user" in data["law"]["evidence"].get("examples", [])


# ---------------------------------------------------------------------------
# End-to-end smoke: mine -> write -> check
# ---------------------------------------------------------------------------


def test_e2e_mine_write_check(snake_project, cli_runner, monkeypatch, tmp_path):
    monkeypatch.chdir(snake_project)
    out_path = snake_project / "roam-laws.yml"

    mine = cli_runner.invoke(
        cli,
        ["--json", "laws", "mine", "--out", str(out_path)],
        catch_exceptions=False,
    )
    assert mine.exit_code == 0, mine.output
    data = parse_json_output(mine, command="laws-mine")
    assert_json_envelope(data, command="laws-mine")
    assert data["summary"]["law_count"] >= 1
    assert out_path.exists()

    # No diff content -> verdict says so
    list_result = cli_runner.invoke(
        cli,
        ["--json", "laws", "list", "--laws-file", str(out_path)],
        catch_exceptions=False,
    )
    list_data = parse_json_output(list_result, command="laws-list")
    assert list_data["summary"]["law_count"] >= 1
