"""Tests for Salesforce extractors and grammar aliasing infrastructure.

Covers:
- ApexExtractor: class/trigger symbols, SOQL refs, label refs, visibility
- SfxmlExtractor: object symbols, sidecar skipping
- AuraExtractor: component symbols, attributes, controller/component refs
- VisualforceExtractor: page symbols, controller/extensions refs, merge fields
- Grammar aliasing: detect_language, alias resolution, extractor routing
- Integration: full project indexing with roam CLI
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from conftest import roam, git_init, git_commit


# ---------------------------------------------------------------------------
# Helper: parse a source string using tree-sitter + extractor
# ---------------------------------------------------------------------------

def _parse_and_extract(source_text: str, file_path: str, language: str = None):
    """Parse source text and extract symbols + references.

    Returns (symbols, references) lists.
    """
    from roam.index.parser import detect_language, GRAMMAR_ALIASES
    from roam.languages.registry import get_extractor
    from tree_sitter_language_pack import get_parser

    if language is None:
        language = detect_language(file_path)
    assert language is not None, f"Could not detect language for {file_path}"

    grammar = GRAMMAR_ALIASES.get(language, language)
    parser = get_parser(grammar)
    source = source_text.encode("utf-8")
    tree = parser.parse(source)

    extractor = get_extractor(language)
    symbols = extractor.extract_symbols(tree, source, file_path)
    references = extractor.extract_references(tree, source, file_path)
    return symbols, references


# ===========================================================================
# Apex tests
# ===========================================================================

class TestApexExtractor:
    """Tests for ApexExtractor (cls/trigger files parsed via Java grammar)."""

    def test_apex_class_symbols(self):
        """Parse a .cls file with public sharing class, verify class is exported and methods extracted."""
        source = (
            "public with sharing class AccountService {\n"
            "    public String getName() {\n"
            "        return 'test';\n"
            "    }\n"
            "\n"
            "    private void doInternal() {\n"
            "        // internal logic\n"
            "    }\n"
            "}\n"
        )
        symbols, refs = _parse_and_extract(source, "force-app/main/classes/AccountService.cls")

        # Find the class symbol
        class_syms = [s for s in symbols if s["kind"] == "class"]
        assert len(class_syms) >= 1, f"Expected at least 1 class symbol, got {len(class_syms)}"
        cls = class_syms[0]
        assert cls["name"] == "AccountService"
        assert cls["is_exported"] is True
        assert cls["visibility"] == "public"
        # Sharing modifier should appear in signature
        assert "with sharing" in cls["signature"]

        # Methods should be extracted
        method_syms = [s for s in symbols if s["kind"] == "method"]
        method_names = {m["name"] for m in method_syms}
        assert "getName" in method_names
        assert "doInternal" in method_names

    def test_apex_trigger(self):
        """Parse a .trigger file, verify trigger symbol is extracted."""
        source = (
            "trigger AccountTrigger on Account (before insert, after update) {\n"
            "    for (Account acc : Trigger.new) {\n"
            "        acc.Name = 'Updated';\n"
            "    }\n"
            "}\n"
        )
        symbols, refs = _parse_and_extract(source, "force-app/main/triggers/AccountTrigger.trigger")

        trigger_syms = [s for s in symbols if s["kind"] == "trigger"]
        assert len(trigger_syms) == 1
        trigger = trigger_syms[0]
        assert trigger["name"] == "AccountTrigger"
        assert trigger["is_exported"] is True
        assert "on Account" in trigger["signature"]

    def test_apex_soql_refs(self):
        """Verify SOQL FROM clauses create soql references."""
        source = (
            "public class QueryExample {\n"
            "    public void run() {\n"
            "        List<Account> accs = [SELECT Id, Name FROM Account WHERE Name != null];\n"
            "        List<Contact> contacts = [SELECT Id FROM Contact__c];\n"
            "    }\n"
            "}\n"
        )
        symbols, refs = _parse_and_extract(source, "force-app/main/classes/QueryExample.cls")

        soql_refs = [r for r in refs if r["kind"] == "soql"]
        soql_targets = {r["target_name"] for r in soql_refs}
        assert "Account" in soql_targets, f"Expected 'Account' in SOQL refs, got {soql_targets}"
        assert "Contact__c" in soql_targets, f"Expected 'Contact__c' in SOQL refs, got {soql_targets}"

    def test_apex_label_refs(self):
        """Verify System.Label.X creates label references."""
        source = (
            "public class LabelExample {\n"
            "    public String getLabel() {\n"
            "        return System.Label.Welcome_Message;\n"
            "    }\n"
            "    public String other() {\n"
            "        return System.Label.Error_NotFound;\n"
            "    }\n"
            "}\n"
        )
        symbols, refs = _parse_and_extract(source, "force-app/main/classes/LabelExample.cls")

        label_refs = [r for r in refs if r["kind"] == "label"]
        label_targets = {r["target_name"] for r in label_refs}
        assert "Label.Welcome_Message" in label_targets
        assert "Label.Error_NotFound" in label_targets

    def test_apex_visibility(self):
        """Verify public/private visibility detection on methods."""
        # Note: 'global' is not a Java keyword, so the Java grammar cannot
        # parse 'global class Foo' as a class_declaration. Instead we test
        # visibility on a public class with mixed-visibility methods.
        source = (
            "public class VisService {\n"
            "    public void pubMethod() {}\n"
            "    private void privMethod() {}\n"
            "    void defaultMethod() {}\n"
            "}\n"
        )
        symbols, refs = _parse_and_extract(source, "force-app/main/classes/VisService.cls")

        class_syms = [s for s in symbols if s["kind"] == "class"]
        assert len(class_syms) >= 1
        cls = class_syms[0]
        assert cls["visibility"] == "public"
        assert cls["is_exported"] is True

        methods = {m["name"]: m for m in symbols if m["kind"] == "method"}
        assert "pubMethod" in methods
        assert methods["pubMethod"]["visibility"] == "public"
        assert "privMethod" in methods
        assert methods["privMethod"]["visibility"] == "private"


# ===========================================================================
# SFXML tests
# ===========================================================================

class TestSfxmlExtractor:
    """Tests for SfxmlExtractor (Salesforce metadata XML files)."""

    def test_sfxml_object_symbols(self):
        """Parse an .object-meta.xml file, verify object symbol is extracted.

        The SfxmlExtractor derives the kind from the file extension segment
        (e.g. 'object' from 'Invoice__c.object-meta.xml') using _TAG_TO_KIND.
        Since the map key is 'customobject' not 'object', the kind defaults
        to 'metadata' for plain .object-meta.xml files.
        """
        source = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<CustomObject xmlns="http://soap.sforce.com/2006/04/metadata">\n'
            '    <deploymentStatus>Deployed</deploymentStatus>\n'
            '    <label>Invoice</label>\n'
            '    <pluralLabel>Invoices</pluralLabel>\n'
            '    <sharingModel>ReadWrite</sharingModel>\n'
            '</CustomObject>\n'
        )
        symbols, refs = _parse_and_extract(
            source,
            "force-app/main/objects/Invoice__c/Invoice__c.object-meta.xml",
            language="sfxml",
        )

        assert len(symbols) >= 1, f"Expected at least 1 symbol, got {len(symbols)}"
        obj_sym = symbols[0]
        assert obj_sym["name"] == "Invoice__c"
        # The kind is derived from _TAG_TO_KIND; 'object' maps to 'metadata' (default)
        assert obj_sym["kind"] == "metadata"
        assert obj_sym["is_exported"] is True
        assert "object" in obj_sym["qualified_name"]

    def test_sfxml_sidecar_skip(self):
        """Verify .cls-meta.xml sidecars produce no symbols."""
        source = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<ApexClass xmlns="http://soap.sforce.com/2006/04/metadata">\n'
            '    <apiVersion>58.0</apiVersion>\n'
            '    <status>Active</status>\n'
            '</ApexClass>\n'
        )
        symbols, refs = _parse_and_extract(
            source,
            "force-app/main/classes/AccountService.cls-meta.xml",
            language="sfxml",
        )

        assert len(symbols) == 0, (
            f"Sidecar .cls-meta.xml should produce no symbols, got {len(symbols)}: "
            f"{[s['name'] for s in symbols]}"
        )


# ===========================================================================
# Aura tests
# ===========================================================================

class TestAuraExtractor:
    """Tests for AuraExtractor (Lightning Aura component files)."""

    def test_aura_component_symbols(self):
        """Parse a .cmp file, verify component + attributes."""
        source = (
            '<aura:component controller="AccountController">\n'
            '    <aura:attribute name="recordId" type="String" />\n'
            '    <aura:attribute name="account" type="Account" />\n'
            '    <aura:method name="refresh" action="{!c.doRefresh}" />\n'
            '    <div>\n'
            '        <p>{!v.account.Name}</p>\n'
            '    </div>\n'
            '</aura:component>\n'
        )
        symbols, refs = _parse_and_extract(
            source,
            "force-app/main/aura/AccountCard/AccountCard.cmp",
        )

        # Component symbol
        comp_syms = [s for s in symbols if s["kind"] == "component"]
        assert len(comp_syms) == 1
        comp = comp_syms[0]
        assert comp["name"] == "AccountCard"
        assert comp["is_exported"] is True

        # Attribute symbols
        attr_syms = [s for s in symbols if s["kind"] == "property"]
        attr_names = {a["name"] for a in attr_syms}
        assert "recordId" in attr_names, f"Expected 'recordId' attribute, got {attr_names}"
        assert "account" in attr_names, f"Expected 'account' attribute, got {attr_names}"

        # Method symbols
        method_syms = [s for s in symbols if s["kind"] == "method"]
        method_names = {m["name"] for m in method_syms}
        assert "refresh" in method_names, f"Expected 'refresh' method, got {method_names}"

    def test_aura_controller_refs(self):
        """Verify controller attribute creates references."""
        source = (
            '<aura:component controller="MyApexController">\n'
            '    <aura:attribute name="data" type="String" />\n'
            '</aura:component>\n'
        )
        symbols, refs = _parse_and_extract(
            source,
            "force-app/main/aura/MyComp/MyComp.cmp",
        )

        controller_refs = [r for r in refs if r["kind"] == "controller"]
        assert len(controller_refs) >= 1
        assert any(r["target_name"] == "MyApexController" for r in controller_refs), (
            f"Expected controller ref to 'MyApexController', got {[r['target_name'] for r in controller_refs]}"
        )

    def test_aura_custom_component_refs(self):
        """Verify <c:CustomChild> creates component_ref references."""
        source = (
            '<aura:component>\n'
            '    <c:CustomChild recordId="{!v.recordId}" />\n'
            '    <c:AnotherWidget />\n'
            '    <lightning:card title="Test">\n'
            '        <p>Content</p>\n'
            '    </lightning:card>\n'
            '</aura:component>\n'
        )
        symbols, refs = _parse_and_extract(
            source,
            "force-app/main/aura/ParentComp/ParentComp.cmp",
        )

        comp_refs = [r for r in refs if r["kind"] == "component_ref"]
        comp_targets = {r["target_name"] for r in comp_refs}
        assert "CustomChild" in comp_targets, f"Expected 'CustomChild' ref, got {comp_targets}"
        assert "AnotherWidget" in comp_targets, f"Expected 'AnotherWidget' ref, got {comp_targets}"
        # lightning: namespace should NOT create component_ref
        assert not any(r["target_name"] == "card" for r in comp_refs), (
            "lightning:card should not create a component_ref"
        )


# ===========================================================================
# Visualforce tests
# ===========================================================================

class TestVisualforceExtractor:
    """Tests for VisualforceExtractor (Visualforce .page and .component files)."""

    def test_vf_page_symbols(self):
        """Parse a .page file, verify page symbol."""
        source = (
            '<apex:page controller="InvoiceController">\n'
            '    <apex:form>\n'
            '        <apex:inputField value="{!Invoice__c.Name}" />\n'
            '    </apex:form>\n'
            '</apex:page>\n'
        )
        symbols, refs = _parse_and_extract(
            source,
            "force-app/main/pages/InvoicePage.page",
        )

        page_syms = [s for s in symbols if s["kind"] == "page"]
        assert len(page_syms) == 1
        page = page_syms[0]
        assert page["name"] == "InvoicePage"
        assert page["is_exported"] is True
        assert "apex:page" in page["signature"]

    def test_vf_controller_refs(self):
        """Verify controller and extensions attributes create references."""
        source = (
            '<apex:page controller="MainController" extensions="ExtA, ExtB">\n'
            '    <apex:outputText value="Hello" />\n'
            '</apex:page>\n'
        )
        symbols, refs = _parse_and_extract(
            source,
            "force-app/main/pages/MultiCtrl.page",
        )

        controller_refs = [r for r in refs if r["kind"] == "controller"]
        targets = {r["target_name"] for r in controller_refs}
        assert "MainController" in targets, f"Expected 'MainController', got {targets}"
        assert "ExtA" in targets, f"Expected 'ExtA' extension ref, got {targets}"
        assert "ExtB" in targets, f"Expected 'ExtB' extension ref, got {targets}"

    def test_vf_merge_fields(self):
        """Verify {!expression} merge field refs are extracted."""
        source = (
            '<apex:page controller="AcctCtrl">\n'
            '    <apex:outputText value="{!Account.Name}" />\n'
            '    <apex:outputText value="{!Contact__c.Email}" />\n'
            '    <apex:outputText value="{!IF(isActive, \'yes\', \'no\')}" />\n'
            '</apex:page>\n'
        )
        symbols, refs = _parse_and_extract(
            source,
            "force-app/main/pages/MergeFields.page",
        )

        merge_refs = [r for r in refs if r["kind"] == "merge_field"]
        merge_targets = {r["target_name"] for r in merge_refs}
        assert "Account" in merge_targets, f"Expected 'Account' merge field, got {merge_targets}"
        assert "Contact__c" in merge_targets, f"Expected 'Contact__c' merge field, got {merge_targets}"
        # IF is a builtin and should not appear
        assert "IF" not in merge_targets, "Builtin 'IF' should be excluded from merge field refs"


# ===========================================================================
# Grammar aliasing tests
# ===========================================================================

class TestGrammarAliasing:
    """Tests for detect_language, grammar alias resolution, extractor routing."""

    def test_detect_language_cls(self):
        """detect_language('foo.cls') should return 'apex'."""
        from roam.index.parser import detect_language

        assert detect_language("foo.cls") == "apex"
        assert detect_language("force-app/main/classes/Bar.cls") == "apex"

    def test_detect_language_trigger(self):
        """detect_language('foo.trigger') should return 'apex'."""
        from roam.index.parser import detect_language

        assert detect_language("foo.trigger") == "apex"

    def test_detect_language_meta_xml(self):
        """detect_language('foo.cls-meta.xml') should return 'sfxml'."""
        from roam.index.parser import detect_language

        assert detect_language("foo.cls-meta.xml") == "sfxml"
        assert detect_language("Account.object-meta.xml") == "sfxml"
        assert detect_language("MyPage.page-meta.xml") == "sfxml"

    def test_detect_language_cmp(self):
        """detect_language('foo.cmp') should return 'aura'."""
        from roam.index.parser import detect_language

        assert detect_language("foo.cmp") == "aura"

    def test_detect_language_page(self):
        """detect_language('foo.page') should return 'visualforce'."""
        from roam.index.parser import detect_language

        assert detect_language("foo.page") == "visualforce"

    def test_grammar_alias_resolution(self):
        """Verify apex files parse with java grammar (alias resolution)."""
        from roam.index.parser import GRAMMAR_ALIASES

        assert GRAMMAR_ALIASES["apex"] == "java"
        assert GRAMMAR_ALIASES["sfxml"] == "html"
        assert GRAMMAR_ALIASES["aura"] == "html"
        assert GRAMMAR_ALIASES["visualforce"] == "html"

    def test_extractor_routing_apex(self):
        """get_extractor('apex') should return an ApexExtractor instance."""
        from roam.languages.registry import get_extractor
        from roam.languages.apex_lang import ApexExtractor

        ext = get_extractor("apex")
        assert isinstance(ext, ApexExtractor)
        assert ext.language_name == "apex"

    def test_extractor_routing_sfxml(self):
        """get_extractor('sfxml') should return a SfxmlExtractor instance."""
        from roam.languages.registry import get_extractor
        from roam.languages.sfxml_lang import SfxmlExtractor

        ext = get_extractor("sfxml")
        assert isinstance(ext, SfxmlExtractor)
        assert ext.language_name == "sfxml"

    def test_extractor_routing_aura(self):
        """get_extractor('aura') should return an AuraExtractor instance."""
        from roam.languages.registry import get_extractor
        from roam.languages.aura_lang import AuraExtractor

        ext = get_extractor("aura")
        assert isinstance(ext, AuraExtractor)
        assert ext.language_name == "aura"

    def test_extractor_routing_visualforce(self):
        """get_extractor('visualforce') should return a VisualforceExtractor."""
        from roam.languages.registry import get_extractor
        from roam.languages.visualforce_lang import VisualforceExtractor

        ext = get_extractor("visualforce")
        assert isinstance(ext, VisualforceExtractor)
        assert ext.language_name == "visualforce"

    def test_parse_file_with_alias(self, tmp_path):
        """Verify parse_file resolves grammar aliases and returns a valid tree."""
        from roam.index.parser import parse_file

        cls_file = tmp_path / "Test.cls"
        cls_file.write_text(
            "public class Test {\n"
            "    public void run() {}\n"
            "}\n"
        )

        tree, source, language = parse_file(cls_file)
        assert tree is not None, "parse_file should return a valid tree for .cls files"
        assert language == "apex"
        assert source is not None


# ===========================================================================
# Integration test: full project indexing
# ===========================================================================

class TestApexProjectIndexing:
    """Integration test: create a temp Salesforce project, index it, verify results."""

    @pytest.fixture(scope="class")
    def sf_project(self, tmp_path_factory):
        """Create a temp Salesforce project with .cls files and index it."""
        proj = tmp_path_factory.mktemp("sf_project")

        # Create directory structure
        classes_dir = proj / "force-app" / "main" / "classes"
        classes_dir.mkdir(parents=True)
        triggers_dir = proj / "force-app" / "main" / "triggers"
        triggers_dir.mkdir(parents=True)
        pages_dir = proj / "force-app" / "main" / "pages"
        pages_dir.mkdir(parents=True)
        aura_dir = proj / "force-app" / "main" / "aura" / "MyComp"
        aura_dir.mkdir(parents=True)

        # Apex class
        (classes_dir / "AccountService.cls").write_text(
            "public with sharing class AccountService {\n"
            "    public List<Account> getAccounts() {\n"
            "        return [SELECT Id, Name FROM Account];\n"
            "    }\n"
            "    public String getLabel() {\n"
            "        return System.Label.App_Title;\n"
            "    }\n"
            "}\n"
        )
        (classes_dir / "AccountService.cls-meta.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<ApexClass xmlns="http://soap.sforce.com/2006/04/metadata">\n'
            '    <apiVersion>58.0</apiVersion>\n'
            '    <status>Active</status>\n'
            '</ApexClass>\n'
        )

        # Second Apex class that calls the first
        (classes_dir / "AccountController.cls").write_text(
            "public class AccountController {\n"
            "    public void handleRequest() {\n"
            "        AccountService svc = new AccountService();\n"
            "        svc.getAccounts();\n"
            "    }\n"
            "}\n"
        )
        (classes_dir / "AccountController.cls-meta.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<ApexClass xmlns="http://soap.sforce.com/2006/04/metadata">\n'
            '    <apiVersion>58.0</apiVersion>\n'
            '    <status>Active</status>\n'
            '</ApexClass>\n'
        )

        # Trigger
        (triggers_dir / "AccountTrigger.trigger").write_text(
            "trigger AccountTrigger on Account (before insert, after update) {\n"
            "    AccountService svc = new AccountService();\n"
            "}\n"
        )
        (triggers_dir / "AccountTrigger.trigger-meta.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<ApexTrigger xmlns="http://soap.sforce.com/2006/04/metadata">\n'
            '    <apiVersion>58.0</apiVersion>\n'
            '    <status>Active</status>\n'
            '</ApexTrigger>\n'
        )

        # Visualforce page
        (pages_dir / "AccountPage.page").write_text(
            '<apex:page controller="AccountController">\n'
            '    <apex:outputText value="{!Account.Name}" />\n'
            '</apex:page>\n'
        )
        (pages_dir / "AccountPage.page-meta.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<ApexPage xmlns="http://soap.sforce.com/2006/04/metadata">\n'
            '    <apiVersion>58.0</apiVersion>\n'
            '</ApexPage>\n'
        )

        # Aura component
        (aura_dir / "MyComp.cmp").write_text(
            '<aura:component controller="AccountService">\n'
            '    <aura:attribute name="recordId" type="String" />\n'
            '</aura:component>\n'
        )

        git_init(proj)
        return proj

    def test_apex_project_indexing(self, sf_project):
        """Run roam index on a Salesforce project, verify symbols and edges exist."""
        output, rc = roam("index", cwd=sf_project)
        assert rc == 0, f"roam index failed (rc={rc}): {output}"

        # Verify symbols are indexed by searching for them
        output, rc = roam("search", "AccountService", cwd=sf_project)
        assert rc == 0, f"roam search failed: {output}"
        assert "AccountService" in output, (
            f"Expected 'AccountService' in search results, got: {output}"
        )

    def test_apex_project_map(self, sf_project):
        """Verify roam map shows the indexed Salesforce files."""
        # Ensure index exists first
        roam("index", cwd=sf_project)

        output, rc = roam("map", cwd=sf_project)
        assert rc == 0, f"roam map failed: {output}"
        assert "AccountService" in output or ".cls" in output, (
            f"Expected Salesforce files in map output: {output}"
        )

    def test_apex_project_deps(self, sf_project):
        """Verify roam deps works on Salesforce class files."""
        roam("index", cwd=sf_project)

        output, rc = roam(
            "deps",
            "force-app/main/classes/AccountController.cls",
            cwd=sf_project,
        )
        assert rc == 0, f"roam deps failed: {output}"
