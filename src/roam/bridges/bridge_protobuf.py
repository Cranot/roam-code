"""Protobuf cross-language bridge: .proto -> generated stubs.

Resolves cross-references between Protocol Buffer definitions and their
generated code in various languages:
- Python: *_pb2.py modules with classes matching message/service names
- Go: *.pb.go files with CamelCase struct names
- Java: OuterClass.MessageName pattern in *OuterClass.java
- C++: *.pb.h / *.pb.cc with namespace::MessageName
- TypeScript/JavaScript: *_pb.ts / *_pb.js
"""
from __future__ import annotations

import os
import re

from roam.bridges.base import LanguageBridge
from roam.bridges.registry import register_bridge


# Proto source extension
_PROTO_EXT = frozenset({".proto"})

# Generated stub file patterns by language
_GENERATED_PATTERNS: dict[str, re.Pattern] = {
    "python": re.compile(r'_pb2\.pyi?$'),
    "go": re.compile(r'\.pb\.go$'),
    "java": re.compile(r'(?:OuterClass|Grpc|Proto)\.java$'),
    "cpp_header": re.compile(r'\.pb\.h$'),
    "cpp_source": re.compile(r'\.pb\.cc$'),
    "typescript": re.compile(r'_pb\.(ts|d\.ts)$'),
    "javascript": re.compile(r'_pb\.js$'),
    "csharp": re.compile(r'\.g\.cs$'),
    "ruby": re.compile(r'_pb\.rb$'),
}

# All target extensions that could be generated from .proto
_TARGET_EXTS = frozenset({
    ".py", ".pyi", ".go", ".java", ".h", ".cc", ".cpp",
    ".ts", ".js", ".cs", ".rb",
})

# Pattern to extract package from proto file symbols
_PROTO_PACKAGE_RE = re.compile(r'package\s+([\w.]+)')


class ProtobufBridge(LanguageBridge):
    """Bridge between .proto definitions and generated language stubs."""

    @property
    def name(self) -> str:
        return "protobuf"

    @property
    def source_extensions(self) -> frozenset[str]:
        return _PROTO_EXT

    @property
    def target_extensions(self) -> frozenset[str]:
        return _TARGET_EXTS

    def detect(self, file_paths: list[str]) -> bool:
        """Detect if project has .proto files and potential generated stubs."""
        has_proto = False
        has_generated = False
        for fp in file_paths:
            ext = os.path.splitext(fp)[1].lower()
            if ext == ".proto":
                has_proto = True
            # Check for generated stub patterns
            basename = os.path.basename(fp)
            for pattern in _GENERATED_PATTERNS.values():
                if pattern.search(basename):
                    has_generated = True
                    break
            if has_proto and has_generated:
                return True
        return False

    def resolve(self, source_path: str, source_symbols: list[dict],
                target_files: dict[str, list[dict]]) -> list[dict]:
        """Resolve .proto symbols to their generated stubs.

        Resolution strategies:
        1. File naming: foo.proto -> foo_pb2.py, foo.pb.go, etc.
        2. Symbol naming: message MyMessage -> class MyMessage (Python),
           struct MyMessage (Go), MyMessage (Java inner class)
        3. Service naming: service MyService -> MyServiceClient, MyServiceServer
        """
        edges: list[dict] = []
        source_ext = os.path.splitext(source_path)[1].lower()

        if source_ext != ".proto":
            return edges

        # Get the proto file stem (e.g., "foo" from "foo.proto")
        proto_stem = os.path.basename(source_path).rsplit(".", 1)[0]

        # Classify source symbols into messages, services, enums
        messages = []
        services = []
        enums = []
        for sym in source_symbols:
            kind = sym.get("kind", "")
            if kind in ("message", "class", "struct"):
                messages.append(sym)
            elif kind in ("service", "interface"):
                services.append(sym)
            elif kind == "enum":
                enums.append(sym)

        # Find generated target files that correspond to this proto
        generated_targets = self._find_generated_files(proto_stem, target_files)

        # For each generated file, try to match symbols
        for tpath, tsymbols, lang in generated_targets:
            target_symbol_names = {
                sym.get("name", ""): sym.get("qualified_name", "")
                for sym in tsymbols
            }

            # Match message symbols
            for msg in messages:
                msg_name = msg.get("name", "")
                msg_qname = msg.get("qualified_name", msg_name)
                matched = self._match_message(msg_name, target_symbol_names, lang)
                for target_qname in matched:
                    edges.append({
                        "source": msg_qname,
                        "target": target_qname,
                        "kind": "x-lang",
                        "bridge": self.name,
                        "mechanism": "proto-message",
                        "target_lang": lang,
                    })

            # Match service symbols
            for svc in services:
                svc_name = svc.get("name", "")
                svc_qname = svc.get("qualified_name", svc_name)
                matched = self._match_service(svc_name, target_symbol_names, lang)
                for target_qname in matched:
                    edges.append({
                        "source": svc_qname,
                        "target": target_qname,
                        "kind": "x-lang",
                        "bridge": self.name,
                        "mechanism": "proto-service",
                        "target_lang": lang,
                    })

            # Match enum symbols
            for enum in enums:
                enum_name = enum.get("name", "")
                enum_qname = enum.get("qualified_name", enum_name)
                matched = self._match_enum(enum_name, target_symbol_names, lang)
                for target_qname in matched:
                    edges.append({
                        "source": enum_qname,
                        "target": target_qname,
                        "kind": "x-lang",
                        "bridge": self.name,
                        "mechanism": "proto-enum",
                        "target_lang": lang,
                    })

        return edges

    def _find_generated_files(self, proto_stem: str,
                              target_files: dict[str, list[dict]]
                              ) -> list[tuple[str, list[dict], str]]:
        """Find target files that were likely generated from this proto.

        Returns list of (path, symbols, language) tuples.
        """
        results = []
        proto_lower = proto_stem.lower()

        for tpath, tsymbols in target_files.items():
            basename = os.path.basename(tpath).lower()

            # Check each language pattern
            for lang, pattern in _GENERATED_PATTERNS.items():
                if pattern.search(basename):
                    # Verify the stem matches the proto file
                    # e.g., "foo_pb2.py" stem is "foo", "foo.pb.go" stem is "foo"
                    generated_stem = self._extract_stem(basename, lang)
                    if generated_stem and generated_stem == proto_lower:
                        results.append((tpath, tsymbols, lang))
                    break  # Only match one language per file

        return results

    def _extract_stem(self, basename: str, lang: str) -> str | None:
        """Extract the original proto stem from a generated filename.

        E.g., "foo_pb2.py" -> "foo", "foo.pb.go" -> "foo"
        """
        lower = basename.lower()
        if lang == "python":
            # foo_pb2.py or foo_pb2.pyi
            m = re.match(r'^(.+)_pb2\.pyi?$', lower)
            return m.group(1) if m else None
        elif lang == "go":
            # foo.pb.go
            m = re.match(r'^(.+)\.pb\.go$', lower)
            return m.group(1) if m else None
        elif lang == "java":
            # FooOuterClass.java or FooGrpc.java or FooProto.java
            m = re.match(r'^(.+?)(?:outerclass|grpc|proto)\.java$', lower)
            return m.group(1) if m else None
        elif lang in ("cpp_header", "cpp_source"):
            # foo.pb.h or foo.pb.cc
            m = re.match(r'^(.+)\.pb\.(?:h|cc)$', lower)
            return m.group(1) if m else None
        elif lang == "typescript":
            # foo_pb.ts or foo_pb.d.ts
            m = re.match(r'^(.+)_pb\.(?:d\.)?ts$', lower)
            return m.group(1) if m else None
        elif lang == "javascript":
            # foo_pb.js
            m = re.match(r'^(.+)_pb\.js$', lower)
            return m.group(1) if m else None
        elif lang == "csharp":
            # Foo.g.cs
            m = re.match(r'^(.+)\.g\.cs$', lower)
            return m.group(1) if m else None
        elif lang == "ruby":
            # foo_pb.rb
            m = re.match(r'^(.+)_pb\.rb$', lower)
            return m.group(1) if m else None
        return None

    def _match_message(self, msg_name: str, target_names: dict[str, str],
                       lang: str) -> list[str]:
        """Match a proto message name to generated symbols.

        Naming conventions vary by language:
        - Python: class MyMessage in *_pb2.py (exact name)
        - Go: struct MyMessage in *.pb.go (CamelCase preserved)
        - Java: inner class MyMessage inside OuterClass
        - C++: class MyMessage in namespace
        """
        matched = []
        msg_lower = msg_name.lower()

        for sym_name, sym_qname in target_names.items():
            sym_lower = sym_name.lower()

            # Exact match (most common for Python, Go, C++)
            if sym_lower == msg_lower:
                matched.append(sym_qname)
                continue

            # Go: proto snake_case -> CamelCase
            # e.g., my_message -> MyMessage
            if lang == "go" and self._snake_to_camel(msg_name).lower() == sym_lower:
                matched.append(sym_qname)
                continue

            # Java: OuterClass.MessageName pattern
            if lang == "java" and sym_lower.endswith("." + msg_lower):
                matched.append(sym_qname)
                continue

        return matched

    def _match_service(self, svc_name: str, target_names: dict[str, str],
                       lang: str) -> list[str]:
        """Match a proto service name to generated symbols.

        Generated service stubs commonly use suffixes:
        - Python: MyServiceStub, MyServiceServicer
        - Go: MyServiceClient, MyServiceServer
        - Java: MyServiceGrpc, MyServiceBlockingStub
        """
        matched = []
        svc_lower = svc_name.lower()

        # Common generated suffixes for service stubs
        suffixes = [
            "",  # exact match
            "client", "server", "stub", "servicer",
            "grpc", "blockingstub", "futurestub",
            "implbase",
        ]

        for sym_name, sym_qname in target_names.items():
            sym_lower = sym_name.lower()
            for suffix in suffixes:
                if sym_lower == svc_lower + suffix:
                    matched.append(sym_qname)
                    break
                # Also check with underscore separator (Python style)
                if suffix and sym_lower == svc_lower + "_" + suffix:
                    matched.append(sym_qname)
                    break

        return matched

    def _match_enum(self, enum_name: str, target_names: dict[str, str],
                    lang: str) -> list[str]:
        """Match a proto enum name to generated symbols.

        Enums generally keep their name across languages.
        """
        matched = []
        enum_lower = enum_name.lower()

        for sym_name, sym_qname in target_names.items():
            sym_lower = sym_name.lower()

            # Exact match
            if sym_lower == enum_lower:
                matched.append(sym_qname)
                continue

            # Go CamelCase conversion
            if lang == "go" and self._snake_to_camel(enum_name).lower() == sym_lower:
                matched.append(sym_qname)
                continue

        return matched

    def _snake_to_camel(self, name: str) -> str:
        """Convert snake_case to CamelCase.

        E.g., my_message -> MyMessage, already_camel -> AlreadyCamel
        """
        if "_" not in name:
            # Already CamelCase or single word; just capitalize first letter
            return name[0].upper() + name[1:] if name else name
        parts = name.split("_")
        return "".join(p.capitalize() for p in parts if p)


# Auto-register on import
register_bridge(ProtobufBridge())
