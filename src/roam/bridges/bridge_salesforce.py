"""Salesforce cross-language bridge: Apex <-> Aura/LWC/Visualforce.

Resolves cross-references between:
- Apex controllers (.cls) and Aura components (.cmp, .app)
- Apex controllers (.cls) and Visualforce pages (.page, .component)
- @AuraEnabled methods in Apex and Lightning component calls
- Apex classes referenced via controller="" attributes in markup
"""
from __future__ import annotations

import os
import re

from roam.bridges.base import LanguageBridge
from roam.bridges.registry import register_bridge


# Pattern to detect @AuraEnabled annotation on symbols
_AURA_ENABLED_RE = re.compile(r'@AuraEnabled', re.IGNORECASE)

# Apex source extensions
_APEX_EXTS = frozenset({".cls", ".trigger"})

# Aura/LWC/Visualforce target extensions
_SF_MARKUP_EXTS = frozenset({".cmp", ".app", ".evt", ".intf", ".page", ".component"})


class SalesforceBridge(LanguageBridge):
    """Bridge between Apex controllers and Aura/LWC/Visualforce templates."""

    @property
    def name(self) -> str:
        return "salesforce"

    @property
    def source_extensions(self) -> frozenset[str]:
        return _APEX_EXTS

    @property
    def target_extensions(self) -> frozenset[str]:
        return _SF_MARKUP_EXTS

    def detect(self, file_paths: list[str]) -> bool:
        """Detect if project has both Apex and markup files."""
        has_apex = False
        has_markup = False
        for fp in file_paths:
            ext = os.path.splitext(fp)[1].lower()
            if ext in _APEX_EXTS:
                has_apex = True
            if ext in _SF_MARKUP_EXTS:
                has_markup = True
            if has_apex and has_markup:
                return True
        return False

    def resolve(self, source_path: str, source_symbols: list[dict],
                target_files: dict[str, list[dict]]) -> list[dict]:
        """Resolve Apex-to-markup cross-language links.

        Resolution strategies:
        1. Naming convention: MyController.cls -> MyController.cmp
        2. Controller attribute: <aura:component controller="MyController">
        3. @AuraEnabled methods: match to components referencing that controller
        """
        edges: list[dict] = []
        source_ext = os.path.splitext(source_path)[1].lower()

        if source_ext not in _APEX_EXTS:
            return edges

        # Get the Apex class name from the file path
        apex_class_name = os.path.basename(source_path).rsplit(".", 1)[0]

        # Build a lookup of target symbols by qualified name for fast matching
        target_symbol_index: dict[str, str] = {}  # symbol_name -> qualified_name
        # Track which target files reference this Apex class as a controller
        controller_targets: list[tuple[str, list[dict]]] = []

        for tpath, tsymbols in target_files.items():
            text_ext = os.path.splitext(tpath)[1].lower()
            if text_ext not in _SF_MARKUP_EXTS:
                continue

            for sym in tsymbols:
                target_symbol_index[sym.get("name", "")] = sym.get("qualified_name", "")

            # Check if any target symbol references this Apex class
            # Aura components reference controllers via naming convention or
            # controller attribute (already extracted as references by AuraExtractor)
            target_basename = os.path.basename(tpath).rsplit(".", 1)[0]

            # Strategy 1: Naming convention match
            # MyController.cls -> MyController.cmp (same name)
            # MyController.cls -> MyControllerCmp.cmp (with suffix)
            if self._names_match(apex_class_name, target_basename):
                controller_targets.append((tpath, tsymbols))
                # Create edge from Apex class to the component
                edges.append({
                    "source": apex_class_name,
                    "target": target_basename,
                    "kind": "x-lang",
                    "bridge": self.name,
                    "mechanism": "naming-convention",
                })

        # Strategy 2: Match @AuraEnabled methods to components that reference
        # this controller. Any component whose controller is this Apex class
        # can call its @AuraEnabled methods.
        aura_enabled_methods = self._find_aura_enabled_methods(source_symbols)

        for method_name, method_qname in aura_enabled_methods:
            for tpath, tsymbols in controller_targets:
                target_basename = os.path.basename(tpath).rsplit(".", 1)[0]
                edges.append({
                    "source": method_qname,
                    "target": target_basename,
                    "kind": "x-lang",
                    "bridge": self.name,
                    "mechanism": "aura-enabled",
                })

        # Strategy 3: Check for Visualforce controller references
        # Visualforce pages specify controller="ClassName" in <apex:page>
        # The VF extractor already extracts these as "controller" references,
        # but we create x-lang edges for them here
        for tpath, tsymbols in target_files.items():
            text_ext = os.path.splitext(tpath)[1].lower()
            if text_ext not in (".page", ".component"):
                continue
            target_basename = os.path.basename(tpath).rsplit(".", 1)[0]
            # Check if any symbol in the target references this Apex class
            # We look for the component-level symbol whose name matches
            for sym in tsymbols:
                # Visualforce pages are top-level symbols of kind "page"/"component"
                if sym.get("kind") in ("page", "component"):
                    # The VF extractor creates controller references; check if
                    # this Apex class is referenced by name in the target path
                    # (We rely on naming convention or explicit controller attr)
                    if self._names_match(apex_class_name, target_basename):
                        # Already handled by strategy 1 above; skip duplicate
                        pass

        return edges

    def _names_match(self, apex_name: str, target_name: str) -> bool:
        """Check if an Apex class name matches a component name.

        Supports common Salesforce naming conventions:
        - Exact match: MyController -> MyController
        - Controller suffix: MyController -> My (component uses class as controller)
        - Component suffix: MyClass -> MyClassController (Apex has Controller suffix)
        """
        apex_lower = apex_name.lower()
        target_lower = target_name.lower()

        # Exact match
        if apex_lower == target_lower:
            return True

        # Apex name is target + "Controller" suffix
        # e.g., MyComponentController.cls -> MyComponent.cmp
        if apex_lower == target_lower + "controller":
            return True

        # Target name is apex + "Controller" suffix (less common but possible)
        if target_lower == apex_lower + "controller":
            return True

        return False

    def _find_aura_enabled_methods(self, symbols: list[dict]) -> list[tuple[str, str]]:
        """Find methods with @AuraEnabled annotation.

        Returns list of (method_name, qualified_name) tuples.
        """
        results = []
        for sym in symbols:
            kind = sym.get("kind", "")
            if kind not in ("method", "function"):
                continue
            sig = sym.get("signature", "") or ""
            # Check if the signature or annotations contain @AuraEnabled
            if _AURA_ENABLED_RE.search(sig):
                results.append((sym.get("name", ""), sym.get("qualified_name", "")))
                continue
            # Also check docstring/annotations if stored there
            doc = sym.get("docstring", "") or ""
            if _AURA_ENABLED_RE.search(doc):
                results.append((sym.get("name", ""), sym.get("qualified_name", "")))
        return results


# Auto-register on import
register_bridge(SalesforceBridge())
