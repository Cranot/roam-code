"""An unreadable file must resolve to UNKNOWN, never to a clean PASS.

``verify-imports`` publishes exactly one file-population cardinality:
``files_checked``. That is a NUMERATOR -- it counts files that yielded
imports, not files the scan was asked to examine. A file that is a row in
``files`` but cannot be read on disk (stale index, moved/deleted source, a
``--path`` naming a file that isn't there) is floored to ``[]`` by
``_scan_file_imports``, which is the SAME value a genuinely import-free file
returns. It therefore drops out of ``all_imports`` and out of
``files_checked`` together, and every unresolved import it contained leaves
the numerator with it.

The failure is the absent-resolves-to-EQUAL shape: "producer identity
absent" (the file was never read) defaults to "producer identity matches"
(the file is clean) instead of to UNKNOWN. It lands on three terminal
verdicts with no disclosure sibling -- the JSON ``summary.verdict`` /
``unresolved: 0`` / ``partial_success: false``, the human "All N imports
verified successfully.", and a SARIF document with zero results that a
code-scanning gate reads as green. The capability is ``mcp_expose=True`` on
the ``core`` preset and its own docstring calls it "a hallucination firewall
for AI-generated code", so an agent reads ``unresolved: 0`` as "the imports
are valid".

The guard converts the implicit equality into an explicit UNKNOWN that fails
closed: the population is published (``files_in_scope``), the unexaminable
members are named (``files_unverifiable`` / ``unverifiable_files``), the
clean verdicts are gated on there being none, and SARIF emits an
``unverifiable-file`` result so the CI sink cannot go green on a scan that
never ran.

NEGATIVE CONTROLS are mandatory here: a "fix" that simply flags everything,
or that suppresses the clean verdict unconditionally, would satisfy every
positive assertion above. ``TestGuardIsInertOnHealthyInput`` fails such a
change -- a healthy project must still reach the clean verdict, and a real
unresolved import must still be reported on its own.
"""

from __future__ import annotations

import json
import os

import click
import pytest
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def stale_index_project(project_factory):
    """Project whose only unresolved import lives in a file that will vanish.

    ``ghost.py`` is the ONLY carrier of an unresolved import. Deleting it
    after indexing removes the entire numerator, so a pre-fix run reports a
    perfectly clean scan -- which is exactly the fail-open under test.
    """
    return project_factory(
        {
            "models.py": "class User:\n    pass\n",
            "service.py": "from models import User\n\ndef get_user():\n    return User()\n",
            "ghost.py": "from nonexistent_module import FakeClass\n\ndef use():\n    return FakeClass()\n",
        }
    )


@pytest.fixture
def healthy_project(project_factory):
    """Project where every import resolves and every file stays readable."""
    return project_factory(
        {
            "models.py": "class User:\n    pass\n",
            "service.py": "from models import User\n\ndef get_user():\n    return User()\n",
        }
    )


def _build_cli():
    from roam.commands.cmd_verify_imports import verify_imports_cmd

    @click.group()
    @click.option("--json", "json_mode", is_flag=True, default=False)
    @click.option("--sarif", "sarif_mode", is_flag=True, default=False)
    @click.pass_context
    def cli(ctx, json_mode, sarif_mode):
        ctx.ensure_object(dict)
        ctx.obj["json"] = json_mode
        ctx.obj["sarif"] = sarif_mode

    cli.add_command(verify_imports_cmd)
    return cli


def _invoke(cwd, *, json_mode=False, sarif_mode=False):
    cli = _build_cli()
    runner = CliRunner()
    args = []
    if json_mode:
        args.append("--json")
    if sarif_mode:
        args.append("--sarif")
    args.append("verify-imports")
    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        return runner.invoke(cli, args, catch_exceptions=False)
    finally:
        os.chdir(old_cwd)


def _break_one_file(project) -> str:
    """Delete the sole unresolved-import carrier WITHOUT re-indexing."""
    target = os.path.join(str(project), "ghost.py")
    os.remove(target)
    return "ghost.py"


# ---------------------------------------------------------------------------
# The fail-open, at each of the three terminal verdicts
# ---------------------------------------------------------------------------


class TestUnreadableFileIsUnknownNotClean:
    def test_connection_result_names_the_population_and_the_gap(self, stale_index_project):
        """The result must publish the population, not just the numerator."""
        from roam.commands.cmd_verify_imports import verify_imports_for_connection
        from roam.db.connection import open_db

        missing = _break_one_file(stale_index_project)
        old_cwd = os.getcwd()
        try:
            os.chdir(str(stale_index_project))
            with open_db(readonly=True) as conn:
                result = verify_imports_for_connection(conn, str(stale_index_project))
        finally:
            os.chdir(old_cwd)

        # Pre-fix: the key does not exist at all -- files_checked was the
        # only cardinality in the result.
        assert missing in result["files_unreadable"]
        # The input population is published, and it exceeds the numerator.
        assert result["files_in_scope"] > result["files_checked"]

    def test_json_verdict_refuses_to_claim_clean(self, stale_index_project):
        """summary.verdict must not assert a clean scan it did not perform."""
        _break_one_file(stale_index_project)
        result = _invoke(stale_index_project, json_mode=True)
        payload = json.loads(result.output)
        summary = payload["summary"]

        # Pre-fix verdict: "All 2 imports resolved across 1 files".
        assert not summary["verdict"].startswith("All ")
        assert "UNKNOWN" in summary["verdict"]
        # An incomplete scan is a partial success by definition.
        assert summary["partial_success"] is True
        assert summary["files_unverifiable"] == 1
        assert summary["files_in_scope"] > summary["files_checked"]
        # The unexaminable member is NAMED, not just counted.
        assert payload["unverifiable_files"] == ["ghost.py"]

    def test_human_output_refuses_to_claim_success(self, stale_index_project):
        """The text sink must not print the success line."""
        _break_one_file(stale_index_project)
        result = _invoke(stale_index_project)

        assert "verified successfully" not in result.output
        assert "UNVERIFIABLE" in result.output
        assert "ghost.py" in result.output

    def test_sarif_does_not_go_green_on_a_scan_that_never_ran(self, stale_index_project):
        """Zero SARIF results is how a code-scanning gate goes green."""
        _break_one_file(stale_index_project)
        result = _invoke(stale_index_project, sarif_mode=True)
        doc = json.loads(result.output)
        results = doc["runs"][0]["results"]

        # Pre-fix: results == [].
        assert len(results) == 1
        assert results[0]["ruleId"] == "unverifiable-file"
        # The rule is declared in the catalogue it is emitted against.
        rule_ids = {r["id"] for r in doc["runs"][0]["tool"]["driver"]["rules"]}
        assert "unverifiable-file" in rule_ids


# ---------------------------------------------------------------------------
# NEGATIVE CONTROLS -- a fix that blocks everything must fail these
# ---------------------------------------------------------------------------


class TestGuardIsInertOnHealthyInput:
    def test_healthy_project_still_reaches_the_clean_verdict(self, healthy_project):
        """Nothing unreadable => the clean verdict is still reachable."""
        result = _invoke(healthy_project, json_mode=True)
        summary = json.loads(result.output)["summary"]

        assert summary["files_unverifiable"] == 0
        assert summary["partial_success"] is False
        assert summary["verdict"].startswith("All ")
        assert "UNKNOWN" not in summary["verdict"]

    def test_healthy_project_still_prints_the_success_line(self, healthy_project):
        result = _invoke(healthy_project)

        assert "verified successfully" in result.output
        assert "UNVERIFIABLE" not in result.output

    def test_healthy_project_sarif_stays_empty(self, healthy_project):
        """No unreadable file and no unresolved import => zero results."""
        result = _invoke(healthy_project, sarif_mode=True)
        doc = json.loads(result.output)

        assert doc["runs"][0]["results"] == []

    def test_real_unresolved_import_is_still_reported_on_a_readable_tree(self, stale_index_project):
        """The detector must keep detecting -- file present, import bad."""
        # NOTE: deliberately does NOT delete ghost.py.
        result = _invoke(stale_index_project, json_mode=True)
        summary = json.loads(result.output)["summary"]

        assert summary["unresolved"] >= 1
        assert summary["files_unverifiable"] == 0
        assert summary["partial_success"] is False
        assert "UNKNOWN" not in summary["verdict"]
