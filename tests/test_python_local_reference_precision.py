"""Local values must not become cross-file callback or impacted-test edges."""

from __future__ import annotations

import json

import pytest
from tree_sitter_language_pack import get_parser

from roam.commands.cmd_affected_tests import _gather_affected_tests
from roam.commands.cmd_verify import _gather_and_rank_tests
from roam.db.connection import open_db
from roam.languages.python_lang import PythonExtractor
from tests.conftest import invoke_cli


def test_reference_definition_change_does_not_reuse_v2_metric_baselines():
    from roam.commands.metrics_history import SNAPSHOT_METRICS_VERSION, is_current_metrics_version

    assert SNAPSHOT_METRICS_VERSION >= 3
    assert not is_current_metrics_version({"metrics_version": 2})


def _refs(source_text):
    source = source_text.encode()
    tree = get_parser("python").parse(source)
    assert not tree.root_node.has_error
    extractor = PythonExtractor()
    extractor.extract_symbols(tree, source, "sample.py")
    return extractor.extract_references(tree, source, "sample.py")


@pytest.mark.parametrize(
    "binding",
    [
        "project = {}",
        "project: dict = {}",
        "project: dict",
        "project = other = {}",
        "project, other = ({}, 1)",
        "[project, *other] = [{}, 1]",
        "project += 1",
        "if False:\n        project = {}",
    ],
)
def test_assigned_local_argument_is_not_a_global_reference(binding):
    refs = _refs(f"def project(): pass\ndef consume(value): pass\ndef use():\n    {binding}\n    consume(project)\n")
    assert not [ref for ref in refs if ref["source_name"] == "use" and ref["target_name"] == "project"]
    assert any(ref["source_name"] == "use" and ref["target_name"] == "consume" for ref in refs)


def test_local_callback_alias_keeps_the_real_definition_reference():
    refs = _refs("def callback(): pass\ndef use():\n    alias = callback\n    register(alias)\n")
    assert any(ref["target_name"] == "callback" and ref["source_name"] == "use" for ref in refs)
    assert not any(ref["target_name"] == "alias" for ref in refs)


@pytest.mark.parametrize(
    "body",
    [
        "global project\n    project = {}\n    register(project)",
        "def nested():\n        project = {}\n    register(project)",
        "class Nested:\n        project = {}\n    register(project)",
        "register(project)",
    ],
)
def test_global_and_unshadowed_callbacks_remain_references(body):
    refs = _refs(f"def project(): pass\ndef use():\n    {body}\n")
    assert any(ref["target_name"] == "project" and ref["source_name"] == "use" for ref in refs)


def test_enclosing_assignment_shadows_global_in_closure():
    refs = _refs("def project(): pass\ndef outer():\n    project = {}\n    def inner():\n        register(project)\n")
    assert not any(ref["target_name"] == "project" for ref in refs)


def test_inner_global_declaration_does_not_bind_to_outer_local():
    refs = _refs(
        "def project(): pass\ndef outer(project):\n    def inner():\n        global project\n        register(project)\n"
    )
    assert any(ref["target_name"] == "project" and ref["source_name"] == "outer.inner" for ref in refs)


def test_parameter_default_runs_in_the_enclosing_scope():
    refs = _refs("def project(): pass\ndef use(project=register(project)):\n    register(project)\n")
    project_refs = [ref for ref in refs if ref["target_name"] == "project"]
    assert len(project_refs) == 1
    assert project_refs[0]["line"] == 2


@pytest.mark.parametrize(
    "parameter", ["project", "project: dict", "project=None", "project: dict=None", "*project", "**project"]
)
def test_parameter_forms_remain_local(parameter):
    refs = _refs(f"def project(): pass\ndef use({parameter}):\n    register(project)\n")
    assert not any(ref["target_name"] == "project" for ref in refs)


@pytest.mark.parametrize("target", ["obj.project", "obj[project]"])
def test_attribute_and_subscript_mutation_do_not_bind_a_local(target):
    refs = _refs(f"def project(): pass\ndef use():\n    {target} = 1\n    register(project)\n")
    assert any(ref["target_name"] == "project" for ref in refs)


def test_nonlocal_assignment_preserves_enclosing_binding():
    refs = _refs(
        "def project(): pass\ndef outer():\n    project = {}\n    def inner():\n        nonlocal project\n        project = {}\n        register(project)\n"
    )
    assert not any(ref["target_name"] == "project" for ref in refs)


def test_alias_chain_retains_callback_without_resolving_local_aliases_globally():
    refs = _refs("def callback(): pass\ndef use():\n    first = callback\n    second = first\n    register(second)\n")
    assert [ref["target_name"] for ref in refs] == ["callback", "register"]


def test_binding_scan_is_cached_per_function_and_reset_per_extraction(monkeypatch):
    extractor = PythonExtractor()
    scans = []
    original = extractor._binding_target_names

    def track(node, source):
        if node is not None and node.type == "identifier":
            scans.append(source[node.start_byte : node.end_byte])
        return original(node, source)

    monkeypatch.setattr(extractor, "_binding_target_names", track)
    source = ("def use():\n    project = {}\n" + "    register(project)\n" * 100).encode()
    tree = get_parser("python").parse(source)
    for _ in range(2):
        refs = extractor.extract_references(tree, source, "sample.py")
        assert not any(ref["target_name"] == "project" for ref in refs)
    # One body scan for 100 references, but no stale cache across generations.
    assert scans == [b"project", b"project"]


def test_real_graph_retains_shared_fixture_without_local_value_fanout(project_factory, monkeypatch):
    project = project_factory(
        {
            "pytest.ini": "[pytest]\n",
            "tests/__init__.py": "",
            "tests/test_provider.py": "import pytest\n@pytest.fixture\ndef project():\n    return {}\ndef test_own(project):\n    assert project == {}\n",
            "tests/test_shared.py": "from tests.test_provider import project\ndef test_shared(project):\n    assert project == {}\n",
            "tests/test_unrelated.py": "def test_unrelated():\n    project = {}\n    assert isinstance(project, dict)\n",
        }
    )
    monkeypatch.chdir(project)
    with open_db(readonly=True) as conn:
        ids = {
            row[0]
            for row in conn.execute(
                "SELECT s.id FROM symbols s JOIN files f ON f.id=s.file_id WHERE f.path='tests/test_provider.py'"
            )
        }
        entries = _gather_affected_tests(conn, ids, [])
        ordered, unavailable = _gather_and_rank_tests(conn, ids, [], ["tests/test_provider.py"], project)
    assert unavailable is None
    assert set(ordered) == {"tests/test_provider.py", "tests/test_shared.py"}, entries


def test_public_verify_runs_the_narrowed_real_test_set(project_factory, cli_runner, monkeypatch):
    project = project_factory(
        {
            "pytest.ini": "[pytest]\n",
            "test_provider.py": "def project():\n    return {}\ndef test_own():\n    assert project() == {}\n",
            "test_unrelated.py": "def test_unrelated():\n    project = {}\n    assert isinstance(project, dict)\n    raise AssertionError('unrelated test must not be selected by a local name')\n",
        }
    )
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    monkeypatch.setenv("PYTEST_ADDOPTS", "")
    result = invoke_cli(cli_runner, ["verify", "--checks", "tests", "test_provider.py"], cwd=project, json_mode=True)
    envelope = json.loads(result.stdout)
    assert result.exit_code == 0, envelope
    tests = envelope["categories"]["tests"]
    assert tests["tests_total_impacted"] == tests["tests_targeted"] == 1
    assert tests["test_execution"]["passed"] == 1
    assert envelope["summary"]["verification_complete"] is True
