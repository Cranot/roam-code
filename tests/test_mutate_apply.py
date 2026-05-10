"""Post-apply file-content tests for ``roam mutate`` destructive paths.

The pre-existing ``tests/test_mutate.py`` only validates the dry-run JSON
shape for ``rename`` / ``add-call`` / ``extract``, plus a single
``_apply_move`` happy-path test. A regression that wrote the wrong bytes
to disk (or a partial failure mid-way through ``_apply_move``) would
ship silently. This module closes that gap by:

1. Asserting the **bytes on disk** after every ``--apply`` for rename,
   add-call, and extract — not just the planned change list.
2. Exercising rename on a fixture with three callers in three separate
   files, so a regression that updates only one caller is caught.
3. Asserting the ``_apply_move`` partial-failure rollback contract:
   when one of the multi-file writes fails mid-way, the source file is
   left unchanged, the target file does NOT contain the moved symbol,
   no duplicate definitions land in the repo, and the returned
   envelope has ``isError: True`` with a structured ``error_code``.
   Both the "newly-created target" and "pre-existing target" branches
   of the rollback are exercised.
"""

from __future__ import annotations

import os
import subprocess

import pytest
from click.testing import CliRunner

from tests.conftest import index_in_process


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _git_init_commit(path):
    """Init + commit so roam's git-aware indexer sees the files."""
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "init"], cwd=str(path), capture_output=True)
    subprocess.run(["git", "add", "."], cwd=str(path), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init", "--allow-empty"],
        cwd=str(path),
        capture_output=True,
        env=env,
    )


@pytest.fixture
def rename_project(tmp_path):
    """Three-caller fixture: ``greet`` is defined in ``svc.py`` and
    imported + called from three independent caller files.

    A correct rename must rewrite the definition AND every caller's
    reference; a regression that forgets one caller will fail loudly.
    """
    proj = tmp_path / "rename_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "svc.py").write_text(
        'def greet(name):\n    return "hello " + name\n\ndef farewell(name):\n    return "bye " + name\n'
    )
    (proj / "caller_a.py").write_text("from svc import greet\n\ndef a():\n    return greet('A')\n")
    (proj / "caller_b.py").write_text("from svc import greet\n\ndef b():\n    return greet('B')\n")
    (proj / "caller_c.py").write_text("from svc import greet\n\ndef c():\n    return greet('C')\n")
    _git_init_commit(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    return proj


@pytest.fixture
def add_call_project(tmp_path):
    """Cross-file add-call fixture: ``payment_flow`` lives in flow.py;
    ``log_event`` lives in helpers.py. Adding a call must (a) insert the
    call inside the body of ``payment_flow`` and (b) add an import for
    ``log_event`` since it's not already imported.
    """
    proj = tmp_path / "addcall_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "helpers.py").write_text("def log_event(msg):\n    print(msg)\n")
    (proj / "flow.py").write_text('def payment_flow(data):\n    amount = data["amount"]\n    return amount * 2\n')
    _git_init_commit(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    return proj


@pytest.fixture
def extract_project(tmp_path):
    """Single-file extract fixture: ``big_function`` has 5 body lines;
    we extract a contiguous middle pair into ``compute_and_print``.
    """
    proj = tmp_path / "extract_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "big.py").write_text(
        'def big_function(data):\n    x = data["a"]\n    y = data["b"]\n    z = x + y\n    print(z)\n    return z\n'
    )
    _git_init_commit(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    return proj


@pytest.fixture
def move_project(tmp_path):
    """Single-symbol fixture used to drive the partial-failure test.

    Kept tiny on purpose — the partial-failure test asserts on file
    contents and existence, so a small surface keeps the assertions
    precise.
    """
    proj = tmp_path / "move_proj"
    proj.mkdir()
    (proj / ".gitignore").write_text(".roam/\n")
    (proj / "src.py").write_text("def hello():\n    return 1\n\ndef other():\n    return 2\n")
    _git_init_commit(proj)
    out, rc = index_in_process(proj)
    assert rc == 0, f"index failed:\n{out}"
    return proj


# ---------------------------------------------------------------------------
# rename --apply: definition + every caller is rewritten
# ---------------------------------------------------------------------------


class TestRenameApply:
    """Assert the renamed symbol's source file is updated AND every
    caller's reference is rewritten. Three callers in three files — a
    bug that updated only one would slip past a smaller fixture.
    """

    def test_apply_rewrites_definition(self, rename_project, monkeypatch):
        monkeypatch.chdir(rename_project)
        from roam.db.connection import open_db
        from roam.refactor.transforms import rename_symbol

        with open_db(readonly=True) as conn:
            result = rename_symbol(conn, "greet", "salutate", dry_run=False)
        assert "error" not in result

        svc_text = (rename_project / "svc.py").read_text()
        # New name landed in the definition line.
        assert "def salutate(name):" in svc_text
        # Old name is gone from svc.py.
        assert "def greet(" not in svc_text
        # Other unrelated symbol survives untouched.
        assert "def farewell(name):" in svc_text

    def test_apply_rewrites_all_three_callers(self, rename_project, monkeypatch):
        monkeypatch.chdir(rename_project)
        from roam.db.connection import open_db
        from roam.refactor.transforms import rename_symbol

        with open_db(readonly=True) as conn:
            rename_symbol(conn, "greet", "salutate", dry_run=False)

        for caller in ("caller_a.py", "caller_b.py", "caller_c.py"):
            text = (rename_project / caller).read_text()
            # Import line rewritten.
            assert "from svc import salutate" in text, f"{caller} import not rewritten:\n{text}"
            # Call site rewritten.
            assert "salutate(" in text, f"{caller} call site not rewritten"
            # No stale reference left behind anywhere in the file.
            assert "greet" not in text, f"{caller} still contains stale 'greet' reference:\n{text}"

    def test_apply_cli_writes_to_disk(self, rename_project, monkeypatch):
        """End-to-end via the CLI to catch any wiring regression in
        cmd_mutate that loses the apply flag.
        """
        monkeypatch.chdir(rename_project)
        from roam.cli import cli

        runner = CliRunner()
        res = runner.invoke(
            cli,
            ["mutate", "rename", "greet", "salutate", "--apply"],
            catch_exceptions=False,
        )
        assert res.exit_code == 0, res.output

        for caller in ("caller_a.py", "caller_b.py", "caller_c.py"):
            text = (rename_project / caller).read_text()
            assert "salutate" in text, f"{caller} not updated by CLI --apply"
            assert "greet" not in text, f"{caller} still has stale name"


# ---------------------------------------------------------------------------
# add-call --apply: call lands in the right body, import is added
# ---------------------------------------------------------------------------


class TestAddCallApply:
    """Assert the inserted call lands in the right function body and
    the import (if any) is added.
    """

    def test_apply_inserts_call_inside_body(self, add_call_project, monkeypatch):
        monkeypatch.chdir(add_call_project)
        from roam.db.connection import open_db
        from roam.refactor.transforms import add_call

        with open_db(readonly=True) as conn:
            result = add_call(conn, "payment_flow", "log_event", args='"hi"', dry_run=False)
        assert "error" not in result, result

        flow_text = (add_call_project / "flow.py").read_text()
        flow_lines = flow_text.splitlines()

        # Call statement is present, indented 4 spaces (function body).
        call_line_idx = next(
            (i for i, line in enumerate(flow_lines) if 'log_event("hi")' in line),
            None,
        )
        assert call_line_idx is not None, f"call not inserted into flow.py:\n{flow_text}"
        assert flow_lines[call_line_idx].startswith("    "), (
            f"call not indented as function body:\n{flow_lines[call_line_idx]!r}"
        )

        # Original body is preserved (we don't delete the existing return).
        assert "    return amount * 2" in flow_text
        assert 'amount = data["amount"]' in flow_text

    def test_apply_adds_import(self, add_call_project, monkeypatch):
        monkeypatch.chdir(add_call_project)
        from roam.db.connection import open_db
        from roam.refactor.transforms import add_call

        with open_db(readonly=True) as conn:
            add_call(conn, "payment_flow", "log_event", args='"hi"', dry_run=False)

        flow_text = (add_call_project / "flow.py").read_text()
        # Import was inserted because helpers.py != flow.py and
        # log_event was not previously imported.
        assert "from helpers import log_event" in flow_text, f"import not added to flow.py:\n{flow_text}"

    def test_apply_no_duplicate_import(self, add_call_project, monkeypatch):
        """If an import for the callee already exists, a second
        ``--apply`` must NOT add a duplicate import line.

        Regression guard: ``_find_import_line`` is fragile (substring
        match) — a future refactor that breaks dedup would silently
        produce two ``from helpers import log_event`` lines.
        """
        monkeypatch.chdir(add_call_project)
        from roam.db.connection import open_db
        from roam.refactor.transforms import add_call

        # First apply adds the import.
        with open_db(readonly=True) as conn:
            add_call(conn, "payment_flow", "log_event", args='"hi"', dry_run=False)

        # Re-index so the DB sees the now-imported state.
        out, rc = index_in_process(add_call_project, "--force")
        assert rc == 0, out

        # Second apply must NOT add a duplicate import.
        with open_db(readonly=True) as conn:
            add_call(conn, "payment_flow", "log_event", args='"again"', dry_run=False)

        flow_text = (add_call_project / "flow.py").read_text()
        import_count = flow_text.count("from helpers import log_event")
        assert import_count == 1, f"duplicate import lines after second apply (got {import_count}):\n{flow_text}"


# ---------------------------------------------------------------------------
# extract --apply: lines move to a new function, original site gets a call
# ---------------------------------------------------------------------------


class TestExtractApply:
    """Assert the extracted lines move to a new function in the target
    file AND the original site is replaced with a call to the new
    function.
    """

    def test_apply_creates_new_function(self, extract_project, monkeypatch):
        monkeypatch.chdir(extract_project)
        from roam.db.connection import open_db
        from roam.refactor.transforms import extract_symbol

        # Extract lines 4-5 ("z = x + y" and "print(z)").
        with open_db(readonly=True) as conn:
            result = extract_symbol(conn, "big_function", 4, 5, "compute_and_print", dry_run=False)
        assert "error" not in result, result

        text = (extract_project / "big.py").read_text()

        # New function definition exists.
        assert "def compute_and_print():" in text, f"new function not created:\n{text}"
        # Extracted body lines moved into the new function (with the
        # leading indent stripped + re-applied at 4 spaces).
        assert "z = x + y" in text
        assert "print(z)" in text

    def test_apply_replaces_extracted_site_with_call(self, extract_project, monkeypatch):
        monkeypatch.chdir(extract_project)
        from roam.db.connection import open_db
        from roam.refactor.transforms import extract_symbol

        with open_db(readonly=True) as conn:
            extract_symbol(conn, "big_function", 4, 5, "compute_and_print", dry_run=False)

        text = (extract_project / "big.py").read_text()
        lines = text.splitlines()

        # The original body of big_function ends with the call to the
        # new function (preserved indent), then ``return z``.
        big_def_idx = next(i for i, line in enumerate(lines) if line.startswith("def big_function"))
        # Walk forward until we hit the next def (the extracted helper).
        body = []
        for line in lines[big_def_idx + 1 :]:
            if line.startswith("def "):
                break
            body.append(line)

        body_text = "\n".join(body)
        assert "compute_and_print()" in body_text, f"call site not replaced with call:\n{body_text}"
        # The two extracted lines are NOT in the original body anymore.
        assert "z = x + y" not in body_text, f"extracted line 'z = x + y' still in original body:\n{body_text}"
        # ``return z`` is still in the original body (we only extracted
        # the middle lines).
        assert "return z" in body_text


# ---------------------------------------------------------------------------
# _apply_move partial-failure: behavior when a mid-flight write fails
# ---------------------------------------------------------------------------


class TestMoveApplyPartialFailure:
    """Probe the destructive path when one of the multi-file writes
    fails mid-way.

    Order of writes inside ``_apply_move``:
      1. Write target file (creates it / appends moved symbol)
      2. Write source file (removes the moved symbol)
      3. Rewrite caller imports (none in this fixture)

    We monkeypatch ``_write_file`` to raise on the SECOND call, after
    step 1 has already succeeded. The contract:

      (a) source file is unchanged
      (b) target file does NOT contain the moved symbol
      (c) returned envelope has ``isError: True`` with a structured
          ``error_code``
      (d) no duplicate definitions land in the repo

    Before the rollback contract was added, ``_apply_move`` left the
    moved symbol duplicated in the new ``dest.py`` and propagated the
    raw ``OSError`` upward — agents had to parse tracebacks to know
    they were in a half-applied state. The test below also serves as a
    regression guard for the rollback path itself.
    """

    def _setup_flaky_write(self, monkeypatch, fail_on_call=2):
        """Patch ``_write_file`` to raise OSError on its Nth call."""
        from roam.refactor import transforms as T

        calls = {"count": 0}
        real_write = T._write_file

        def flaky(path, lines):
            calls["count"] += 1
            if calls["count"] == fail_on_call:
                raise OSError("simulated disk failure")
            real_write(path, lines)

        monkeypatch.setattr(T, "_write_file", flaky)
        return calls

    def test_partial_failure_rolls_back_cleanly(self, move_project, monkeypatch):
        """End-to-end rollback assertion covering (a), (b), (c), (d).

        Asserting all four properties in one test ensures we describe
        the rollback as an atomic contract rather than four loosely
        coupled side effects.
        """
        monkeypatch.chdir(move_project)
        from roam.db.connection import open_db
        from roam.refactor.transforms import move_symbol

        original_src = (move_project / "src.py").read_text()
        self._setup_flaky_write(monkeypatch, fail_on_call=2)

        with open_db(readonly=True) as conn:
            # No raise: rollback wraps the OSError into a structured
            # envelope so agents can parse the failure mode.
            result = move_symbol(conn, "hello", "dest.py", dry_run=False)

        # (c) Structured error envelope.
        assert result.get("isError") is True, result
        assert result.get("error_code") == "APPLY_FAILED", result
        assert "files_modified" in result and result["files_modified"] == []

        # (a) Source file is byte-for-byte unchanged.
        assert (move_project / "src.py").read_text() == original_src

        # (b) Target file does NOT contain the moved symbol. The target
        # didn't exist before the apply, so the cleanest rollback is to
        # remove it entirely.
        if (move_project / "dest.py").exists():
            dest_text = (move_project / "dest.py").read_text()
            assert "def hello(" not in dest_text, (
                f"dest.py still contains the moved symbol after rollback:\n{dest_text}"
            )

        # (d) Across the entire repo there is at most one definition
        # of ``hello`` (the original, in src.py).
        defs = [str(py) for py in move_project.rglob("*.py") if "def hello(" in py.read_text()]
        assert len(defs) == 1, f"expected exactly one 'hello' definition (the original); got {defs}"
        assert defs[0].endswith("src.py")

    def test_partial_failure_preserves_existing_target_file(self, move_project, monkeypatch):
        """Variant: target file already exists with unrelated content.

        Rollback must restore the original bytes of the pre-existing
        target file, NOT delete it (deleting an existing file would be
        worse than the original bug). Guards the
        ``target_existed`` branch in the rollback path.
        """
        monkeypatch.chdir(move_project)
        existing_target = move_project / "dest.py"
        existing_content = "def already_here():\n    return 'untouched'\n"
        existing_target.write_text(existing_content)
        # Re-index so the existing target is part of the DB. Not
        # strictly required for ``move`` (the indexer doesn't influence
        # target reads), but keeps the fixture consistent with how a
        # user would invoke this in practice.
        out, rc = index_in_process(move_project, "--force")
        assert rc == 0, out

        from roam.db.connection import open_db
        from roam.refactor.transforms import move_symbol

        original_src = (move_project / "src.py").read_text()
        self._setup_flaky_write(monkeypatch, fail_on_call=2)

        with open_db(readonly=True) as conn:
            result = move_symbol(conn, "hello", "dest.py", dry_run=False)

        assert result.get("isError") is True
        assert result.get("error_code") == "APPLY_FAILED"

        # Source unchanged.
        assert (move_project / "src.py").read_text() == original_src
        # Pre-existing target was restored, not deleted, and does not
        # contain the moved symbol.
        assert existing_target.exists(), "rollback deleted a pre-existing target file"
        restored = existing_target.read_text()
        assert "def already_here():" in restored, f"existing target content lost during rollback:\n{restored}"
        assert "def hello(" not in restored, f"moved symbol leaked into pre-existing target after rollback:\n{restored}"
