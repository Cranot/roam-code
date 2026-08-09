"""An import roam has no dependency model for is UNKNOWN, not a hallucination.

``verify-imports`` carries a real model of "where an import may legitimately
come from without being in the index" for exactly two languages: Python
(``sys.stdlib_module_names``) and Node/JS (a 42-entry builtin list plus the
nearest package.json). Every OTHER language falls through to
``_check_name_exists`` against the indexed symbol/file tables -- and a repo's
index never contains that language's standard library. Measured on a valid
two-language repo: 9 of 9 Go and Java imports published as ``unresolved``, 4 of
them escalating to SARIF ``hallucination-import`` at ``level: error`` with the
message "no nearby symbol in the indexed table -- hallucinated import". ``fmt``,
``os`` and ``java.util.List`` are not hallucinations.

The TWIN defect lives on the same line and points the other way. The old guard
was a blocklist of one::

    stdlib_scope = is_py or (not is_js_like and lang_lower != "go")

Java is not "go", so Java imports were tested against the PYTHON stdlib list,
first-dotted-segment matched. ``import io.totallyfake.doesnotexist.Nope;`` -- a
Java import of a package that exists nowhere -- was published RESOLVED, because
``io`` is a Python stdlib module. The firewall passed a fabricated import clean.

The fix publishes a third status rather than forcing an answer the producer
does not have: ``unverifiable``. The row is EMITTED (dropping it would delete
the firewall for Go and Java outright and ship a zero-result SARIF document a
code-scanning gate reads as clean), leaves the ``unresolved`` numerator, gets
its own bucket plus ``incomplete_reasons``, and projects to SARIF as
``unverifiable-import`` at ``warning`` -- never ``error``.

NEGATIVE CONTROLS are mandatory. A "fix" that skipped every import in a
language without a model, or that broadened ``unverifiable`` to swallow the
Python rows, would satisfy the positive assertions above.
``TestPythonBehaviourIsUnchanged`` and ``test_go_rows_are_emitted_not_dropped``
fail such a change.
"""

from __future__ import annotations

import json
import os

import click
import pytest
from click.testing import CliRunner

GO_MAIN = 'package main\n\nimport (\n\t"encoding/json"\n\t"fmt"\n\t"net/http"\n\t"os"\n)\n\nfunc main() {\n\tfmt.Println(os.Args, json.Valid(nil), http.StatusOK)\n}\n'

JAVA_APP = (
    "import java.util.List;\n"
    "import java.util.Map;\n"
    "import io.totallyfake.doesnotexist.Nope;\n"
    "\n"
    "public class App {\n"
    "    public static void main(String[] args) {}\n"
    "}\n"
)

PY_CTL = "import os\nimport json\nimport totallyfakepkg123\n\n\ndef ctl():\n    return os, json, totallyfakepkg123\n"


@pytest.fixture
def polyglot_project(project_factory):
    """Go + Java + a Python control, all valid, all indexed."""
    return project_factory(
        {
            "go.mod": "module example.com/demo\n\ngo 1.21\n",
            "cmd/main.go": GO_MAIN,
            "java/App.java": JAVA_APP,
            "ctl.py": PY_CTL,
        }
    )


@pytest.fixture
def python_only_project(project_factory):
    """No Go, no Java -- the measured-correct Python behaviour must survive."""
    return project_factory({"ctl.py": PY_CTL})


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


def _rows(project):
    result = _invoke(project, json_mode=True)
    payload = json.loads(result.output)
    return payload["summary"], payload["imports"], result.output


# ---------------------------------------------------------------------------
# MUST FIRE
# ---------------------------------------------------------------------------


class TestNoModelLanguagesAreUnverifiable:
    def test_go_stdlib_is_not_called_unresolved(self, polyglot_project):
        _summary, rows, _out = _rows(polyglot_project)
        go_rows = [r for r in rows if r["file"].endswith("main.go")]
        assert go_rows, "the Go file contributed no import rows at all"
        assert all(r["status"] == "unverifiable" for r in go_rows), go_rows

    def test_java_stdlib_is_not_called_unresolved(self, polyglot_project):
        _summary, rows, _out = _rows(polyglot_project)
        java_rows = [r for r in rows if r["file"].endswith("App.java")]
        assert java_rows
        assert all(r["status"] == "unverifiable" for r in java_rows), java_rows

    def test_fabricated_java_package_no_longer_reads_as_resolved(self, polyglot_project):
        """``io.totallyfake.doesnotexist.Nope`` matched Python's ``io``."""
        _summary, rows, _out = _rows(polyglot_project)
        fake = [r for r in rows if r["name"] == "io.totallyfake.doesnotexist.Nope"]
        assert fake, "the fabricated Java import was not scanned at all"
        assert fake[0]["status"] != "resolved", fake[0]

    def test_summary_names_the_coverage_hole(self, polyglot_project):
        summary, _rows_, _out = _rows(polyglot_project)
        assert summary["unverifiable"] > 0
        assert summary["partial_success"] is True
        reason = summary["incomplete_reasons"][0]
        assert reason.startswith("no_stdlib_model_for_language: ")
        assert "go" in reason and "java" in reason

    def test_text_output_names_the_rows(self, polyglot_project):
        result = _invoke(polyglot_project)
        assert "UNVERIFIABLE" in result.output
        assert "cmd/main.go" in result.output.replace("\\", "/")

    def test_no_suggestion_is_offered_for_an_undecided_row(self, polyglot_project):
        """``strings`` -> ``App.main`` is an invitation to a wrong edit."""
        _summary, rows, _out = _rows(polyglot_project)
        for r in rows:
            if r["status"] == "unverifiable":
                assert not r.get("suggestions"), r


class TestSarifProjection:
    def test_go_rows_are_emitted_not_dropped(self, polyglot_project):
        """Dropping them would delete the firewall for Go and Java."""
        result = _invoke(polyglot_project, sarif_mode=True)
        doc = json.loads(result.output)
        results = doc["runs"][0]["results"]
        go_results = [r for r in results if "main.go" in json.dumps(r["locations"])]
        assert go_results, "the Go rows vanished from SARIF entirely"
        assert all(r["ruleId"] == "unverifiable-import" for r in go_results)

    def test_unverifiable_never_escalates_to_error(self, polyglot_project):
        result = _invoke(polyglot_project, sarif_mode=True)
        doc = json.loads(result.output)
        for r in doc["runs"][0]["results"]:
            if r["ruleId"] == "unverifiable-import":
                assert r["level"] == "warning", r

    def test_python_hallucination_still_escalates_to_error(self, polyglot_project):
        """The firewall must keep blocking where it CAN decide."""
        result = _invoke(polyglot_project, sarif_mode=True)
        doc = json.loads(result.output)
        errors = [r for r in doc["runs"][0]["results"] if r["level"] == "error"]
        assert any("totallyfakepkg123" in r["message"]["text"] for r in errors), errors


# ---------------------------------------------------------------------------
# MUST NOT FIRE -- the negative controls
# ---------------------------------------------------------------------------


class TestPythonBehaviourIsUnchanged:
    def test_python_stdlib_still_resolves(self, python_only_project):
        _summary, rows, _out = _rows(python_only_project)
        by_name = {r["name"]: r["status"] for r in rows}
        assert by_name.get("os") == "resolved", by_name
        assert by_name.get("json") == "resolved", by_name

    def test_python_hallucination_still_unresolved(self, python_only_project):
        _summary, rows, _out = _rows(python_only_project)
        by_name = {r["name"]: r["status"] for r in rows}
        assert by_name.get("totallyfakepkg123") == "unresolved", by_name

    def test_python_only_envelope_carries_no_degradation_keys(self, python_only_project):
        """The healthy envelope must stay byte-compatible for consumers."""
        summary, _rows_, _out = _rows(python_only_project)
        assert "unverifiable" not in summary
        assert "incomplete_reasons" not in summary
        assert summary["partial_success"] is False

    def test_python_only_text_output_still_reports_the_hallucination(self, python_only_project):
        result = _invoke(python_only_project)
        assert "totallyfakepkg123" in result.output
        assert "UNVERIFIABLE" not in result.output


class TestSetsAreExplicitNotDerived:
    def test_python_and_js_are_never_unverifiable(self):
        from roam.commands.cmd_verify_imports import _import_is_unverifiable

        for lang in ("python", "javascript", "typescript", "tsx", "vue", "svelte"):
            assert _import_is_unverifiable(lang) is False, lang

    def test_go_and_java_are_unverifiable(self):
        from roam.commands.cmd_verify_imports import _import_is_unverifiable

        for lang in ("go", "java", "kotlin", "rust", "ruby", "php"):
            assert _import_is_unverifiable(lang) is True, lang

    def test_unknown_language_is_not_swept_in(self):
        """An unrecognised host keeps the Python-shaped fallback path.

        ``_extract_import_names_from_line`` routes any non-JS host through the
        Python regexes, so a ``python -c "import json"`` heredoc in a .yml CI
        step IS Python-shaped and the Python model applies to it. Widening
        ``unverifiable`` to "any language not in the model map" would silently
        stop deciding those rows.
        """
        from roam.commands.cmd_verify_imports import _import_is_unverifiable

        assert _import_is_unverifiable("yaml") is False
        assert _import_is_unverifiable(None) is False
        assert _import_is_unverifiable("") is False
