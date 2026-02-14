"""Tests for the cross-language bridge framework (Phase 3).

Covers:
- LanguageBridge ABC cannot be instantiated directly
- Bridge registry: register, get, detect
- SalesforceBridge: detect(), resolve(), properties
- ProtobufBridge: detect(), resolve(), properties
- detect_bridges() integration
"""
from __future__ import annotations

import pytest

from roam.bridges.base import LanguageBridge
from roam.bridges import registry as bridge_registry
from roam.bridges.bridge_salesforce import SalesforceBridge
from roam.bridges.bridge_protobuf import ProtobufBridge


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_registry():
    """Clear the global bridge registry for isolation."""
    bridge_registry._BRIDGES.clear()


# ---------------------------------------------------------------------------
# LanguageBridge ABC
# ---------------------------------------------------------------------------

class TestLanguageBridgeABC:
    def test_cannot_instantiate_directly(self):
        """LanguageBridge is abstract and cannot be instantiated."""
        with pytest.raises(TypeError):
            LanguageBridge()

    def test_subclass_must_implement_all(self):
        """A subclass missing any abstract member cannot be instantiated."""

        class IncompleteBridge(LanguageBridge):
            @property
            def name(self):
                return "incomplete"

        with pytest.raises(TypeError):
            IncompleteBridge()


# ---------------------------------------------------------------------------
# Bridge registry
# ---------------------------------------------------------------------------

class TestBridgeRegistry:
    def setup_method(self):
        _reset_registry()

    def teardown_method(self):
        _reset_registry()

    def test_registry_starts_empty_after_clear(self):
        assert bridge_registry.get_bridges() == []

    def test_register_and_get(self):
        bridge = SalesforceBridge()
        bridge_registry.register_bridge(bridge)
        bridges = bridge_registry.get_bridges()
        assert len(bridges) == 1
        assert bridges[0].name == "salesforce"

    def test_register_multiple(self):
        bridge_registry.register_bridge(SalesforceBridge())
        bridge_registry.register_bridge(ProtobufBridge())
        assert len(bridge_registry.get_bridges()) == 2

    def test_get_bridges_returns_copy(self):
        bridge_registry.register_bridge(SalesforceBridge())
        b1 = bridge_registry.get_bridges()
        b2 = bridge_registry.get_bridges()
        assert b1 is not b2

    def test_detect_bridges_finds_salesforce(self):
        bridge_registry.register_bridge(SalesforceBridge())
        files = ["src/MyController.cls", "src/MyComponent.cmp"]
        detected = bridge_registry.detect_bridges(files)
        assert len(detected) == 1
        assert detected[0].name == "salesforce"

    def test_detect_bridges_finds_protobuf(self):
        bridge_registry.register_bridge(ProtobufBridge())
        files = ["api/user.proto", "gen/user_pb2.py"]
        detected = bridge_registry.detect_bridges(files)
        assert len(detected) == 1
        assert detected[0].name == "protobuf"

    def test_detect_bridges_returns_empty_for_no_match(self):
        bridge_registry.register_bridge(SalesforceBridge())
        files = ["main.py", "utils.py"]
        detected = bridge_registry.detect_bridges(files)
        assert detected == []


# ---------------------------------------------------------------------------
# SalesforceBridge
# ---------------------------------------------------------------------------

class TestSalesforceBridge:
    def setup_method(self):
        self.bridge = SalesforceBridge()

    def test_name_property(self):
        assert self.bridge.name == "salesforce"

    def test_source_extensions(self):
        exts = self.bridge.source_extensions
        assert ".cls" in exts
        assert ".trigger" in exts

    def test_target_extensions(self):
        exts = self.bridge.target_extensions
        assert ".cmp" in exts
        assert ".page" in exts
        assert ".app" in exts

    def test_detect_true_for_cls_and_cmp(self):
        files = ["force-app/classes/AccountCtrl.cls", "force-app/aura/Account/Account.cmp"]
        assert self.bridge.detect(files) is True

    def test_detect_false_for_only_cls(self):
        files = ["force-app/classes/AccountCtrl.cls"]
        assert self.bridge.detect(files) is False

    def test_detect_false_for_unrelated(self):
        files = ["main.py", "go.mod", "README.md"]
        assert self.bridge.detect(files) is False

    def test_resolve_naming_convention(self):
        """MyController.cls -> MyController.cmp via naming convention."""
        source_path = "classes/MyController.cls"
        source_symbols = [{"name": "MyController", "kind": "class", "qualified_name": "MyController"}]
        target_files = {
            "aura/MyController/MyController.cmp": [
                {"name": "MyController", "kind": "component", "qualified_name": "MyController"}
            ]
        }
        edges = self.bridge.resolve(source_path, source_symbols, target_files)
        assert len(edges) >= 1
        edge = edges[0]
        assert edge["kind"] == "x-lang"
        assert edge["bridge"] == "salesforce"
        assert edge["mechanism"] == "naming-convention"

    def test_resolve_controller_suffix(self):
        """MyComponentController.cls -> MyComponent.cmp via controller suffix."""
        source_path = "classes/MyComponentController.cls"
        source_symbols = [{"name": "MyComponentController", "kind": "class",
                           "qualified_name": "MyComponentController"}]
        target_files = {
            "aura/MyComponent/MyComponent.cmp": [
                {"name": "MyComponent", "kind": "component", "qualified_name": "MyComponent"}
            ]
        }
        edges = self.bridge.resolve(source_path, source_symbols, target_files)
        assert any(e["mechanism"] == "naming-convention" for e in edges)

    def test_resolve_aura_enabled_methods(self):
        """@AuraEnabled methods create x-lang edges to matched components."""
        source_path = "classes/MyController.cls"
        source_symbols = [
            {"name": "MyController", "kind": "class", "qualified_name": "MyController"},
            {"name": "getData", "kind": "method", "qualified_name": "MyController.getData",
             "signature": "@AuraEnabled public static List<Account> getData()"},
        ]
        target_files = {
            "aura/MyController/MyController.cmp": [
                {"name": "MyController", "kind": "component", "qualified_name": "MyController"}
            ]
        }
        edges = self.bridge.resolve(source_path, source_symbols, target_files)
        aura_edges = [e for e in edges if e.get("mechanism") == "aura-enabled"]
        assert len(aura_edges) >= 1
        assert aura_edges[0]["source"] == "MyController.getData"

    def test_resolve_returns_empty_for_non_apex(self):
        """Non-Apex source files produce no edges."""
        edges = self.bridge.resolve("main.py", [], {})
        assert edges == []


# ---------------------------------------------------------------------------
# ProtobufBridge
# ---------------------------------------------------------------------------

class TestProtobufBridge:
    def setup_method(self):
        self.bridge = ProtobufBridge()

    def test_name_property(self):
        assert self.bridge.name == "protobuf"

    def test_source_extensions(self):
        assert ".proto" in self.bridge.source_extensions

    def test_target_extensions(self):
        exts = self.bridge.target_extensions
        assert ".py" in exts
        assert ".go" in exts
        assert ".java" in exts

    def test_detect_true_for_proto_and_pb2(self):
        files = ["api/user.proto", "gen/user_pb2.py"]
        assert self.bridge.detect(files) is True

    def test_detect_true_for_proto_and_pb_go(self):
        files = ["api/user.proto", "gen/user.pb.go"]
        assert self.bridge.detect(files) is True

    def test_detect_false_for_only_proto(self):
        files = ["api/user.proto"]
        assert self.bridge.detect(files) is False

    def test_detect_false_for_unrelated(self):
        files = ["main.py", "utils.go"]
        assert self.bridge.detect(files) is False

    def test_resolve_message_python(self):
        """Proto message -> Python _pb2.py class."""
        source_path = "api/user.proto"
        source_symbols = [
            {"name": "User", "kind": "message", "qualified_name": "api.User"},
        ]
        target_files = {
            "gen/user_pb2.py": [
                {"name": "User", "kind": "class", "qualified_name": "gen.user_pb2.User"},
            ]
        }
        edges = self.bridge.resolve(source_path, source_symbols, target_files)
        assert len(edges) >= 1
        assert edges[0]["source"] == "api.User"
        assert edges[0]["target"] == "gen.user_pb2.User"
        assert edges[0]["mechanism"] == "proto-message"

    def test_resolve_message_go(self):
        """Proto message -> Go .pb.go struct."""
        source_path = "api/user.proto"
        source_symbols = [
            {"name": "User", "kind": "message", "qualified_name": "api.User"},
        ]
        target_files = {
            "gen/user.pb.go": [
                {"name": "User", "kind": "struct", "qualified_name": "gen.User"},
            ]
        }
        edges = self.bridge.resolve(source_path, source_symbols, target_files)
        assert len(edges) >= 1
        assert edges[0]["mechanism"] == "proto-message"
        assert edges[0]["target_lang"] == "go"

    def test_resolve_service_python(self):
        """Proto service -> Python stub/servicer."""
        source_path = "api/greeter.proto"
        source_symbols = [
            {"name": "Greeter", "kind": "service", "qualified_name": "api.Greeter"},
        ]
        target_files = {
            "gen/greeter_pb2.py": [
                {"name": "GreeterStub", "kind": "class",
                 "qualified_name": "gen.greeter_pb2.GreeterStub"},
                {"name": "GreeterServicer", "kind": "class",
                 "qualified_name": "gen.greeter_pb2.GreeterServicer"},
            ]
        }
        edges = self.bridge.resolve(source_path, source_symbols, target_files)
        assert len(edges) >= 2
        targets = {e["target"] for e in edges}
        assert "gen.greeter_pb2.GreeterStub" in targets
        assert "gen.greeter_pb2.GreeterServicer" in targets

    def test_resolve_enum(self):
        """Proto enum -> generated enum class."""
        source_path = "api/status.proto"
        source_symbols = [
            {"name": "StatusCode", "kind": "enum", "qualified_name": "api.StatusCode"},
        ]
        target_files = {
            "gen/status_pb2.py": [
                {"name": "StatusCode", "kind": "class",
                 "qualified_name": "gen.status_pb2.StatusCode"},
            ]
        }
        edges = self.bridge.resolve(source_path, source_symbols, target_files)
        assert len(edges) >= 1
        assert edges[0]["mechanism"] == "proto-enum"

    def test_resolve_returns_empty_for_non_proto(self):
        edges = self.bridge.resolve("main.py", [], {})
        assert edges == []

    def test_resolve_no_match_when_stem_differs(self):
        """No edges when the proto stem doesn't match the target stem."""
        source_path = "api/user.proto"
        source_symbols = [
            {"name": "User", "kind": "message", "qualified_name": "api.User"},
        ]
        target_files = {
            "gen/order_pb2.py": [
                {"name": "Order", "kind": "class", "qualified_name": "gen.order_pb2.Order"},
            ]
        }
        edges = self.bridge.resolve(source_path, source_symbols, target_files)
        assert edges == []
