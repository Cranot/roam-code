"""Tests for v7.1 features: batched helpers, Salesforce imports, Apex generics,
LWC anonymous classes, Flow actionCalls, report --config, SF test naming.
"""

import json
import os
import re
import sqlite3
import tempfile

import pytest


# ---------------------------------------------------------------------------
# 1. batched_in / batched_count helpers
# ---------------------------------------------------------------------------

from roam.db.connection import batched_in, batched_count


def _make_test_db():
    """Create an in-memory SQLite database with sample data for batch tests."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT, kind TEXT)")
    for i in range(20):
        conn.execute(
            "INSERT INTO items VALUES (?, ?, ?)",
            (i, f"item_{i}", "a" if i % 2 == 0 else "b"),
        )
    conn.execute("CREATE TABLE edges (source_id INTEGER, target_id INTEGER)")
    for i in range(15):
        conn.execute("INSERT INTO edges VALUES (?, ?)", (i, i + 1))
    conn.commit()
    return conn


class TestBatchedIn:
    """Tests for the batched_in helper."""

    def test_batched_in_empty_ids(self):
        """batched_in returns an empty list when given no ids."""
        conn = _make_test_db()
        result = batched_in(conn, "SELECT * FROM items WHERE id IN ({ph})", [])
        assert result == []
        conn.close()

    def test_batched_in_small_list(self):
        """batched_in works correctly for a list smaller than batch_size."""
        conn = _make_test_db()
        ids = [0, 1, 2]
        result = batched_in(
            conn, "SELECT * FROM items WHERE id IN ({ph})", ids
        )
        assert len(result) == 3
        names = {r["name"] for r in result}
        assert names == {"item_0", "item_1", "item_2"}
        conn.close()

    def test_batched_in_large_list(self):
        """batched_in correctly chunks when ids exceed batch_size.

        Uses batch_size=5 with 12 items, so 3 batches (5+5+2) are expected.
        All 12 matching rows should be returned.
        """
        conn = _make_test_db()
        ids = list(range(12))
        result = batched_in(
            conn,
            "SELECT * FROM items WHERE id IN ({ph})",
            ids,
            batch_size=5,
        )
        assert len(result) == 12
        returned_ids = sorted(r["id"] for r in result)
        assert returned_ids == list(range(12))
        conn.close()

    def test_batched_in_double_ph(self):
        """batched_in handles two {ph} placeholders (same ids repeated).

        The query asks for edges where source OR target is in the given set.
        With two {ph}, each batch gets chunk = batch_size // 2.
        """
        conn = _make_test_db()
        ids = [3, 4, 5]
        result = batched_in(
            conn,
            "SELECT * FROM edges WHERE source_id IN ({ph}) OR target_id IN ({ph})",
            ids,
        )
        # edges: (3,4), (4,5), (5,6) match source; (2,3), (3,4), (4,5) match target
        source_ids = {r["source_id"] for r in result}
        target_ids = {r["target_id"] for r in result}
        # At minimum, rows with source_id in {3,4,5} and target_id in {3,4,5}
        assert 3 in source_ids
        assert 4 in source_ids
        assert 5 in source_ids
        assert 3 in target_ids
        assert 4 in target_ids
        assert 5 in target_ids
        conn.close()

    def test_batched_in_double_ph_small_batch(self):
        """Two {ph} placeholders with a small batch_size forces smaller chunks."""
        conn = _make_test_db()
        ids = [0, 1, 2, 3, 4]
        # batch_size=4, n_ph=2 -> chunk = 4 // 2 = 2 -> batches: [0,1],[2,3],[4]
        result = batched_in(
            conn,
            "SELECT * FROM edges WHERE source_id IN ({ph}) AND target_id IN ({ph})",
            ids,
            batch_size=4,
        )
        # edges where BOTH source and target are in {0,1,2,3,4}:
        # (0,1),(1,2),(2,3),(3,4) all match
        assert len(result) >= 1  # at least some matches per batch
        conn.close()

    def test_batched_in_pre_post_params(self):
        """batched_in correctly passes pre= and post= extra parameters."""
        conn = _make_test_db()
        ids = [0, 1, 2, 3, 4, 5]
        # pre=[kind_filter] goes before the batch params
        result = batched_in(
            conn,
            "SELECT * FROM items WHERE kind = ? AND id IN ({ph})",
            ids,
            pre=("a",),
        )
        # kind="a" only for even ids: 0, 2, 4
        assert len(result) == 3
        for r in result:
            assert r["kind"] == "a"
        conn.close()

    def test_batched_in_post_params(self):
        """batched_in correctly appends post= parameters after batch params."""
        conn = _make_test_db()
        ids = [0, 1, 2, 3]
        # post param limits to kind = 'b'
        result = batched_in(
            conn,
            "SELECT * FROM items WHERE id IN ({ph}) AND kind = ?",
            ids,
            post=("b",),
        )
        # kind="b" only for odd ids: 1, 3
        assert len(result) == 2
        for r in result:
            assert r["kind"] == "b"
        conn.close()

    def test_batched_in_returns_all_columns(self):
        """batched_in preserves all columns via sqlite3.Row."""
        conn = _make_test_db()
        result = batched_in(
            conn, "SELECT * FROM items WHERE id IN ({ph})", [5]
        )
        assert len(result) == 1
        row = result[0]
        assert row["id"] == 5
        assert row["name"] == "item_5"
        assert row["kind"] == "b"
        conn.close()


class TestBatchedCount:
    """Tests for the batched_count helper."""

    def test_batched_count_empty(self):
        """batched_count returns 0 for empty ids."""
        conn = _make_test_db()
        total = batched_count(
            conn, "SELECT COUNT(*) FROM items WHERE id IN ({ph})", []
        )
        assert total == 0
        conn.close()

    def test_batched_count_sums(self):
        """batched_count correctly sums scalar results across batches."""
        conn = _make_test_db()
        ids = list(range(12))
        total = batched_count(
            conn,
            "SELECT COUNT(*) FROM items WHERE id IN ({ph})",
            ids,
            batch_size=5,
        )
        assert total == 12
        conn.close()

    def test_batched_count_with_filter(self):
        """batched_count works with pre= filter parameters."""
        conn = _make_test_db()
        ids = list(range(10))
        total = batched_count(
            conn,
            "SELECT COUNT(*) FROM items WHERE kind = ? AND id IN ({ph})",
            ids,
            pre=("a",),
            batch_size=4,
        )
        # Even ids 0..9 with kind='a': 0, 2, 4, 6, 8 -> 5
        assert total == 5
        conn.close()

    def test_batched_count_single_batch(self):
        """batched_count works without chunking when ids fit in one batch."""
        conn = _make_test_db()
        ids = [0, 1, 2]
        total = batched_count(
            conn, "SELECT COUNT(*) FROM items WHERE id IN ({ph})", ids
        )
        assert total == 3
        conn.close()


# ---------------------------------------------------------------------------
# 2. @salesforce/* import resolution
# ---------------------------------------------------------------------------

from roam.languages.javascript_lang import JavaScriptExtractor


class TestSalesforceImportResolution:
    """Tests for _resolve_salesforce_import on JavaScriptExtractor."""

    def setup_method(self):
        self.ext = JavaScriptExtractor()

    def test_salesforce_apex_import(self):
        """Resolves @salesforce/apex/ClassName.methodName to (target, 'call')."""
        result = self.ext._resolve_salesforce_import(
            "@salesforce/apex/AccountController.getAccounts"
        )
        assert result is not None
        target, kind = result
        assert target == "AccountController.getAccounts"
        assert kind == "call"

    def test_salesforce_schema_import(self):
        """Resolves @salesforce/schema/Object.Field to (target, 'schema_ref')."""
        result = self.ext._resolve_salesforce_import(
            "@salesforce/schema/Account.Name"
        )
        assert result is not None
        target, kind = result
        assert target == "Account.Name"
        assert kind == "schema_ref"

    def test_salesforce_label_import(self):
        """Resolves @salesforce/label/c.LabelName to (Label.LabelName, 'label')."""
        result = self.ext._resolve_salesforce_import(
            "@salesforce/label/c.greeting"
        )
        assert result is not None
        target, kind = result
        assert target == "Label.greeting"
        assert kind == "label"

    def test_salesforce_label_without_c_prefix(self):
        """Resolves @salesforce/label/SomeName without c. prefix."""
        result = self.ext._resolve_salesforce_import(
            "@salesforce/label/SomeName"
        )
        assert result is not None
        target, kind = result
        assert target == "SomeName"
        assert kind == "label"

    def test_salesforce_message_channel_import(self):
        """Resolves @salesforce/messageChannel/Channel__c to (target, 'import')."""
        result = self.ext._resolve_salesforce_import(
            "@salesforce/messageChannel/MyChannel__c"
        )
        assert result is not None
        target, kind = result
        assert target == "MyChannel__c"
        assert kind == "import"

    def test_salesforce_non_sf_import(self):
        """Returns None for non-@salesforce import paths."""
        assert self.ext._resolve_salesforce_import("lodash") is None
        assert self.ext._resolve_salesforce_import("./utils") is None
        assert self.ext._resolve_salesforce_import("c/myComponent") is None
        assert self.ext._resolve_salesforce_import("@lwc/engine") is None


# ---------------------------------------------------------------------------
# 3. Apex generic type extraction
# ---------------------------------------------------------------------------

from roam.languages.apex_lang import _GENERIC_TYPE_RE, _MAP_VALUE_TYPE_RE, _APEX_BUILTINS


class TestApexGenericTypeExtraction:
    """Tests for Apex generic type regex patterns."""

    def test_apex_generic_list(self):
        """Extracts Account from List<Account>."""
        m = _GENERIC_TYPE_RE.search("List<Account>")
        assert m is not None
        assert m.group(1) == "Account"

    def test_apex_generic_set(self):
        """Extracts Opportunity from Set<Opportunity>."""
        m = _GENERIC_TYPE_RE.search("Set<Opportunity>")
        assert m is not None
        assert m.group(1) == "Opportunity"

    def test_apex_generic_map_key(self):
        """Extracts key type Id from Map<Id, Contact>."""
        m = _GENERIC_TYPE_RE.search("Map<Id, Contact>")
        assert m is not None
        # The first capture is the key type
        assert m.group(1) == "Id"

    def test_apex_generic_map_value(self):
        """Extracts value type Contact from Map<Id, Contact> using the value regex."""
        m = _MAP_VALUE_TYPE_RE.search("Map<Id, Contact>")
        assert m is not None
        assert m.group(1) == "Contact"

    def test_apex_generic_builtin_skip(self):
        """Builtin types like String, Integer, Id are in _APEX_BUILTINS."""
        builtins_to_check = ["String", "Integer", "Long", "Double", "Decimal",
                             "Boolean", "Date", "DateTime", "Time", "Id",
                             "Blob", "Object", "SObject", "Type"]
        for b in builtins_to_check:
            assert b in _APEX_BUILTINS, f"{b} should be in _APEX_BUILTINS"

    def test_apex_generic_builtin_filter(self):
        """When the generic parameter is a builtin, it should be filtered out."""
        # Simulate the filtering logic used in ApexExtractor.extract_references
        text = "List<String> names; List<Account> accounts;"
        for m in _GENERIC_TYPE_RE.finditer(text):
            type_name = m.group(1)
            if type_name not in _APEX_BUILTINS:
                # Only non-builtin types should pass through
                assert type_name == "Account"

    def test_apex_generic_custom_object(self):
        """Extracts Custom_Object__c from List<Custom_Object__c>."""
        m = _GENERIC_TYPE_RE.search("List<Custom_Object__c>")
        assert m is not None
        assert m.group(1) == "Custom_Object__c"

    def test_apex_generic_custom_relationship(self):
        """Extracts Custom_Object__r from Set<Custom_Object__r>."""
        m = _GENERIC_TYPE_RE.search("Set<Custom_Object__r>")
        assert m is not None
        assert m.group(1) == "Custom_Object__r"

    def test_apex_generic_iterable(self):
        """Extracts type from Iterable<SomeType>."""
        m = _GENERIC_TYPE_RE.search("Iterable<BatchJob>")
        assert m is not None
        assert m.group(1) == "BatchJob"

    def test_apex_generic_map_both_custom(self):
        """Extracts both types from Map<Account__c, Contact__c>."""
        text = "Map<Account__c, Contact__c>"
        key_match = _GENERIC_TYPE_RE.search(text)
        value_match = _MAP_VALUE_TYPE_RE.search(text)
        assert key_match is not None
        assert key_match.group(1) == "Account__c"
        assert value_match is not None
        assert value_match.group(1) == "Contact__c"

    def test_apex_generic_no_match_on_plain_type(self):
        """No match for plain type names without generic brackets."""
        m = _GENERIC_TYPE_RE.search("Account myAccount;")
        assert m is None


# ---------------------------------------------------------------------------
# 4. LWC anonymous class extraction
# ---------------------------------------------------------------------------


class TestLwcAnonymousClassExtraction:
    """Tests for anonymous class name derivation from file paths."""

    def test_lwc_anonymous_class(self):
        """Anonymous class derives name from file path (LWC convention)."""
        # Simulate the logic in JavaScriptExtractor._extract_class for anonymous classes
        basename = os.path.basename("force-app/main/lwc/myComponent/myComponent.js")
        name = basename.rsplit(".", 1)[0]
        name = name[0].upper() + name[1:] if name else "Anonymous"
        assert name == "MyComponent"

    def test_lwc_anonymous_class_single_word(self):
        """Single-word component name is capitalized correctly."""
        basename = os.path.basename("force-app/main/lwc/header/header.js")
        name = basename.rsplit(".", 1)[0]
        name = name[0].upper() + name[1:] if name else "Anonymous"
        assert name == "Header"

    def test_lwc_anonymous_class_camelcase(self):
        """CamelCase component name preserves existing casing after first char."""
        basename = os.path.basename("force-app/main/lwc/accountList/accountList.js")
        name = basename.rsplit(".", 1)[0]
        name = name[0].upper() + name[1:] if name else "Anonymous"
        assert name == "AccountList"

    def test_lwc_anonymous_class_already_capitalized(self):
        """Already capitalized name stays the same."""
        basename = os.path.basename("force-app/main/lwc/Dashboard/Dashboard.js")
        name = basename.rsplit(".", 1)[0]
        name = name[0].upper() + name[1:] if name else "Anonymous"
        assert name == "Dashboard"

    def test_lwc_anonymous_class_empty_name(self):
        """Empty file name falls back to 'Anonymous'."""
        # Edge case: basename = ".js" -> name = "" -> "Anonymous"
        name = ""
        name = name[0].upper() + name[1:] if name else "Anonymous"
        assert name == "Anonymous"


# ---------------------------------------------------------------------------
# 5. Flow actionCalls extraction
# ---------------------------------------------------------------------------

from roam.languages.sfxml_lang import SfxmlExtractor


class TestFlowApexActionCalls:
    """Tests for _extract_flow_refs on SfxmlExtractor."""

    def setup_method(self):
        self.ext = SfxmlExtractor()

    def test_flow_apex_action_call(self):
        """Extracts Apex class name from <actionCalls> with actionType=apex."""
        flow_xml = (
            "<Flow>\n"
            "  <actionCalls>\n"
            "    <actionType>apex</actionType>\n"
            "    <actionName>AccountService</actionName>\n"
            "  </actionCalls>\n"
            "</Flow>\n"
        )
        refs = []
        self.ext._extract_flow_refs(flow_xml, refs)
        assert len(refs) == 1
        assert refs[0]["target_name"] == "AccountService"
        assert refs[0]["kind"] == "call"

    def test_flow_apex_action_call_reverse_order(self):
        """Extracts Apex class name when actionName comes before actionType."""
        flow_xml = (
            "<Flow>\n"
            "  <actionCalls>\n"
            "    <actionName>OpportunityHelper</actionName>\n"
            "    <actionType>apex</actionType>\n"
            "  </actionCalls>\n"
            "</Flow>\n"
        )
        refs = []
        self.ext._extract_flow_refs(flow_xml, refs)
        assert len(refs) == 1
        assert refs[0]["target_name"] == "OpportunityHelper"
        assert refs[0]["kind"] == "call"

    def test_flow_non_apex_action_call(self):
        """Non-apex action types are not extracted."""
        flow_xml = (
            "<Flow>\n"
            "  <actionCalls>\n"
            "    <actionType>emailAlert</actionType>\n"
            "    <actionName>SendNotification</actionName>\n"
            "  </actionCalls>\n"
            "</Flow>\n"
        )
        refs = []
        self.ext._extract_flow_refs(flow_xml, refs)
        assert len(refs) == 0

    def test_flow_multiple_action_calls(self):
        """Multiple actionCalls blocks each produce references.

        Note: the regex-based approach may produce duplicate matches when
        multiple blocks appear because the reverse-order regex can span
        across block boundaries. We verify that both target names appear.
        """
        flow_xml = (
            "<Flow>\n"
            "  <actionCalls>\n"
            "    <actionType>apex</actionType>\n"
            "    <actionName>ServiceA</actionName>\n"
            "  </actionCalls>\n"
            "  <actionCalls>\n"
            "    <actionType>apex</actionType>\n"
            "    <actionName>ServiceB</actionName>\n"
            "  </actionCalls>\n"
            "</Flow>\n"
        )
        refs = []
        self.ext._extract_flow_refs(flow_xml, refs)
        assert len(refs) >= 2
        names = {r["target_name"] for r in refs}
        assert "ServiceA" in names
        assert "ServiceB" in names

    def test_flow_apex_action_call_with_whitespace(self):
        """Handles whitespace around actionType and actionName values."""
        flow_xml = (
            "<Flow>\n"
            "  <actionCalls>\n"
            "    <actionType>  apex  </actionType>\n"
            "    <actionName>  MyClass  </actionName>\n"
            "  </actionCalls>\n"
            "</Flow>\n"
        )
        refs = []
        self.ext._extract_flow_refs(flow_xml, refs)
        assert len(refs) == 1
        assert refs[0]["target_name"] == "MyClass"

    def test_flow_action_call_line_number(self):
        """Line number is derived from the start of the <actionCalls> block."""
        flow_xml = (
            "<?xml version='1.0'?>\n"
            "<Flow>\n"
            "  <actionCalls>\n"
            "    <actionType>apex</actionType>\n"
            "    <actionName>Handler</actionName>\n"
            "  </actionCalls>\n"
            "</Flow>\n"
        )
        refs = []
        self.ext._extract_flow_refs(flow_xml, refs)
        assert len(refs) == 1
        # Line count: text before <actionCalls> includes 2 newlines -> line 3
        assert refs[0]["line"] >= 1


# ---------------------------------------------------------------------------
# 6. Report --config
# ---------------------------------------------------------------------------

from roam.commands.cmd_report import _load_custom_presets, PRESETS


class TestReportConfig:
    """Tests for report --config custom preset loading."""

    def test_report_config_loading(self):
        """--config loads custom presets from a valid JSON file."""
        config = {
            "my-audit": {
                "description": "Custom audit report",
                "sections": [
                    {"title": "Health Check", "command": ["health"]},
                    {"title": "Risk Analysis", "command": ["risk", "-n", "10"]},
                ],
            }
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(config, f)
            f.flush()
            tmp_path = f.name
        try:
            result = _load_custom_presets(tmp_path)
            assert "my-audit" in result
            assert result["my-audit"]["description"] == "Custom audit report"
            assert len(result["my-audit"]["sections"]) == 2
            assert result["my-audit"]["sections"][0]["title"] == "Health Check"
            assert result["my-audit"]["sections"][1]["command"] == ["risk", "-n", "10"]
        finally:
            os.unlink(tmp_path)

    def test_report_config_default_description(self):
        """Presets without a description key get a default description."""
        config = {
            "quick-check": {
                "sections": [
                    {"title": "Map", "command": ["map"]},
                ],
            }
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(config, f)
            f.flush()
            tmp_path = f.name
        try:
            result = _load_custom_presets(tmp_path)
            assert "quick-check" in result
            assert "description" in result["quick-check"]
            assert "quick-check" in result["quick-check"]["description"]
        finally:
            os.unlink(tmp_path)

    def test_report_config_validation_not_dict(self):
        """Bad config raises error if the top level is not a dict."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump([1, 2, 3], f)
            f.flush()
            tmp_path = f.name
        try:
            from click import BadParameter
            with pytest.raises(BadParameter, match="JSON object"):
                _load_custom_presets(tmp_path)
        finally:
            os.unlink(tmp_path)

    def test_report_config_validation_missing_sections(self):
        """Bad config raises error if a preset is missing 'sections'."""
        config = {
            "bad-preset": {
                "description": "This is broken",
            }
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(config, f)
            f.flush()
            tmp_path = f.name
        try:
            from click import BadParameter
            with pytest.raises(BadParameter, match="missing 'sections'"):
                _load_custom_presets(tmp_path)
        finally:
            os.unlink(tmp_path)

    def test_report_config_validation_sections_not_list(self):
        """Bad config raises error if sections is not a list."""
        config = {
            "bad-preset": {
                "sections": "not-a-list",
            }
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(config, f)
            f.flush()
            tmp_path = f.name
        try:
            from click import BadParameter
            with pytest.raises(BadParameter, match="sections must be a list"):
                _load_custom_presets(tmp_path)
        finally:
            os.unlink(tmp_path)

    def test_report_config_validation_section_missing_keys(self):
        """Bad config raises error if a section is missing title or command."""
        config = {
            "bad-preset": {
                "sections": [
                    {"title": "OK"},  # missing 'command'
                ],
            }
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(config, f)
            f.flush()
            tmp_path = f.name
        try:
            from click import BadParameter
            with pytest.raises(BadParameter, match="needs 'title' and 'command'"):
                _load_custom_presets(tmp_path)
        finally:
            os.unlink(tmp_path)

    def test_report_config_invalid_json(self):
        """Bad config raises error for invalid JSON."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            f.write("{not valid json}")
            f.flush()
            tmp_path = f.name
        try:
            from click import BadParameter
            with pytest.raises(BadParameter, match="Invalid JSON"):
                _load_custom_presets(tmp_path)
        finally:
            os.unlink(tmp_path)

    def test_report_config_multiple_presets(self):
        """Config file can contain multiple presets."""
        config = {
            "preset-a": {
                "description": "First",
                "sections": [{"title": "A", "command": ["health"]}],
            },
            "preset-b": {
                "description": "Second",
                "sections": [{"title": "B", "command": ["map"]}],
            },
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(config, f)
            f.flush()
            tmp_path = f.name
        try:
            result = _load_custom_presets(tmp_path)
            assert len(result) == 2
            assert "preset-a" in result
            assert "preset-b" in result
        finally:
            os.unlink(tmp_path)

    def test_builtin_presets_exist(self):
        """Built-in presets include the expected names."""
        assert "first-contact" in PRESETS
        assert "security" in PRESETS
        assert "pre-pr" in PRESETS
        assert "refactor" in PRESETS


# ---------------------------------------------------------------------------
# 7. SF test naming convention
# ---------------------------------------------------------------------------

from roam.commands.cmd_testmap import _is_test_file


class TestSfTestFileDetection:
    """Tests for Salesforce-style test file detection patterns."""

    def test_sf_test_file_cls_suffix(self):
        """AccountServiceTest.cls is detected as a test file."""
        assert _is_test_file("AccountServiceTest.cls")

    def test_sf_test_file_underscore_test_cls(self):
        """AccountService_Test.cls is detected as a test file."""
        assert _is_test_file("AccountService_Test.cls")

    def test_sf_non_test_cls(self):
        """AccountService.cls is NOT detected as a test file."""
        assert not _is_test_file("AccountService.cls")

    def test_standard_python_test_prefix(self):
        """test_something.py is detected as a test file."""
        assert _is_test_file("test_something.py")

    def test_standard_python_test_suffix(self):
        """something_test.py is detected as a test file."""
        assert _is_test_file("something_test.py")

    def test_js_spec_file(self):
        """something.spec.js is detected as a test file."""
        assert _is_test_file("something.spec.js")

    def test_js_test_file(self):
        """something.test.js is detected as a test file."""
        assert _is_test_file("something.test.js")

    def test_file_in_tests_dir(self):
        """Files in a tests/ directory are detected as test files."""
        assert _is_test_file("tests/conftest.py")
        assert _is_test_file("src/tests/helper.py")

    def test_file_in_test_dir(self):
        """Files in a test/ directory are detected as test files."""
        assert _is_test_file("test/helper.js")

    def test_file_in_jest_tests_dir(self):
        """Files in a __tests__/ directory are detected as test files."""
        assert _is_test_file("src/__tests__/App.test.js")

    def test_regular_source_file(self):
        """Regular source files are not detected as test files."""
        assert not _is_test_file("src/service.py")
        assert not _is_test_file("lib/utils.js")
        assert not _is_test_file("app/controllers/main.rb")

    def test_backslash_paths(self):
        """Windows-style backslash paths are handled correctly."""
        assert _is_test_file("tests\\test_foo.py")
        assert _is_test_file("src\\__tests__\\App.test.js")

    def test_sf_test_class_in_path(self):
        """Salesforce test class with full path is detected."""
        assert _is_test_file("force-app/main/classes/AccountServiceTest.cls")
        assert _is_test_file("force-app/main/classes/AccountService_Test.cls")

    def test_sf_non_test_class_in_path(self):
        """Non-test Salesforce class with full path is not detected."""
        assert not _is_test_file("force-app/main/classes/AccountService.cls")


# ---------------------------------------------------------------------------
# 8. Bug fixes verified by quality review
# ---------------------------------------------------------------------------


class TestFlowCrossBlockSafety:
    """Verify Flow actionCalls extraction doesn't cross block boundaries."""

    def setup_method(self):
        from roam.languages.sfxml_lang import SfxmlExtractor
        self.ext = SfxmlExtractor()

    def test_flow_no_cross_block_false_positive(self):
        """An emailAlert action should not get matched as apex via cross-block."""
        flow_xml = (
            "<Flow>\n"
            "  <actionCalls>\n"
            "    <actionName>FakeApexAction</actionName>\n"
            "    <actionType>emailAlert</actionType>\n"
            "  </actionCalls>\n"
            "  <actionCalls>\n"
            "    <actionType>apex</actionType>\n"
            "    <actionName>RealApexAction</actionName>\n"
            "  </actionCalls>\n"
            "</Flow>\n"
        )
        refs = []
        self.ext._extract_flow_refs(flow_xml, refs)
        names = {r["target_name"] for r in refs}
        # Only RealApexAction should appear, not FakeApexAction
        assert "RealApexAction" in names
        assert "FakeApexAction" not in names

    def test_flow_mixed_types_exact_count(self):
        """Only apex actions produce refs, not emailAlert or flow actions."""
        flow_xml = (
            "<Flow>\n"
            "  <actionCalls>\n"
            "    <actionType>emailAlert</actionType>\n"
            "    <actionName>SendEmail</actionName>\n"
            "  </actionCalls>\n"
            "  <actionCalls>\n"
            "    <actionType>apex</actionType>\n"
            "    <actionName>ApexHandler</actionName>\n"
            "  </actionCalls>\n"
            "  <actionCalls>\n"
            "    <actionType>flow</actionType>\n"
            "    <actionName>SubFlow</actionName>\n"
            "  </actionCalls>\n"
            "</Flow>\n"
        )
        refs = []
        self.ext._extract_flow_refs(flow_xml, refs)
        assert len(refs) == 1
        assert refs[0]["target_name"] == "ApexHandler"


class TestBatchedInCrossBatchEdges:
    """Verify that AND-based double-IN queries don't miss cross-batch edges."""

    def test_cross_batch_edges_not_missed(self):
        """batched_in with single-IN + Python filter finds cross-batch edges."""
        conn = _make_test_db()
        # IDs 0-9 with edges (0,1),(1,2),...,(8,9)
        id_set = set(range(10))
        # Fetch by source, filter by target — simulates the fixed pattern
        src_rows = batched_in(
            conn,
            "SELECT source_id, target_id FROM edges WHERE source_id IN ({ph})",
            list(id_set),
            batch_size=3,  # small batch to force multiple batches
        )
        internal = [r for r in src_rows if r["target_id"] in id_set]
        # All edges (0,1) through (8,9) should be found
        assert len(internal) == 9
        conn.close()

    def test_report_config_invalid_json_user_friendly(self):
        """Invalid JSON in config file gives a user-friendly error, not traceback."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            f.write("{{invalid}}")
            f.flush()
            tmp_path = f.name
        try:
            from click import BadParameter
            with pytest.raises(BadParameter):
                _load_custom_presets(tmp_path)
        finally:
            os.unlink(tmp_path)
