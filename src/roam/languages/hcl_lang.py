"""HCL / Terraform language extractor (regex-only, no tree-sitter).

Supports Terraform (.tf, .tfvars) and generic HCL (.hcl) files including
Terragrunt, Packer, and Nomad job specs.

Symbols extracted
-----------------
Terraform
  - ``resource "type" "name"``       → kind "class",    qualified_name "type.name"
  - ``data "type" "name"``           → kind "class",    qualified_name "data.type.name"
  - ``variable "name"``              → kind "variable"
  - ``output "name"``                → kind "function"
  - ``module "name"``                → kind "module"
  - ``locals { key = … }``           → kind "variable" (one per local key)
  - ``provider "name"``              → kind "module"
  - ``terraform { … }``             → kind "module"  (singleton, name "terraform")

Packer
  - ``source "type" "name"``         → kind "class",    qualified_name "type.name"
  - ``build { … }``                  → kind "function"

Nomad / generic HCL
  - ``job "name" { … }``             → kind "function"
  - ``task "name" { … }``            → kind "function"
  - ``group "name" { … }``           → kind "function"

References extracted
--------------------
* ``var.name``                        → kind "call"   (variable reference)
* ``module.name``                     → kind "call"   (module output reference)
* ``module.name.output``              → kind "call"   (module output reference)
* ``data.type.name``                  → kind "call"   (data source reference)
* ``<resource_type>.<resource_name>`` → kind "call"   (resource reference)
* ``local.name``                      → kind "call"   (local value reference)
* ``each.key`` / ``each.value``       → skipped (built-in)
* ``path.*`` / ``terraform.*``        → skipped (built-in)
"""

from __future__ import annotations

import logging
import re

from .base import LanguageExtractor

log = logging.getLogger(__name__)

# -- Regex patterns ------------------------------------------------------------

# Block openers: keyword "label1" "label2" {
_RE_BLOCK2 = re.compile(
    r'^([a-z_]+)\s+"([^"]+)"\s+"([^"]+)"\s*\{',
)
# Block opener: keyword "label" {
_RE_BLOCK1 = re.compile(
    r'^([a-z_]+)\s+"([^"]+)"\s*\{',
)
# Block opener without quotes: keyword {  (e.g. `terraform {`, `build {`)
_RE_BLOCK0 = re.compile(
    r"^([a-z_]+)\s*\{",
)

# locals { … } contents — key = value  inside a locals block
_RE_LOCAL_KEY = re.compile(r"^\s+([a-z_][a-zA-Z0-9_]*)\s*=")

# Reference patterns (applied to every line)
_RE_VAR_REF = re.compile(r"\bvar\.([a-z_][a-zA-Z0-9_-]*)\b")
_RE_MODULE_REF = re.compile(r"\bmodule\.([a-z_][a-zA-Z0-9_-]*)(?:\.([a-z_][a-zA-Z0-9_-]*))?\b")
_RE_DATA_REF = re.compile(r"\bdata\.([a-z_][a-zA-Z0-9_-]*)\.([a-z_][a-zA-Z0-9_-]*)\b")
_RE_LOCAL_REF = re.compile(r"\blocal\.([a-z_][a-zA-Z0-9_-]*)\b")

# Resource self-references: aws_vpc.main, google_compute_instance.vm etc.
# Must look like <provider_resource_type>.<name> where type contains "_"
_RE_RESOURCE_REF = re.compile(r"\b([a-z][a-z0-9]*_[a-z][a-zA-Z0-9_]*)\.([a-z_][a-zA-Z0-9_-]*)\b")

# Built-in namespaces to skip when scanning resource refs
_BUILTIN_NAMESPACES = frozenset(
    {
        "var",
        "module",
        "data",
        "local",
        "each",
        "count",
        "path",
        "terraform",
        "self",
        "null",
    }
)

# HCL comment markers
_RE_COMMENT = re.compile(r"^\s*(?:#|//)")

# Detect .tfvars files (key = value only, no block declarations)
_RE_TFVARS_ASSIGNMENT = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*=")


def _line_number(idx: int) -> int:
    return idx + 1


class HclExtractor(LanguageExtractor):
    """Regex-only extractor for HCL / Terraform configuration files."""

    _BLOCK1_METADATA = {
        "variable": ("variable", "variable", True),
        "output": ("function", "output", True),
        "module": ("module", "module", True),
        "provider": ("module", "provider", False),
        "job": ("function", "job", True),
        "task": ("function", "task", True),
        "group": ("function", "group", True),
        "build": ("function", "build", True),
    }

    _BLOCK0_METADATA = {
        "terraform": ("module", False),
        "build": ("function", True),
    }

    @property
    def language_name(self) -> str:
        return "hcl"

    @property
    def file_extensions(self) -> list[str]:
        return [".tf", ".hcl", ".tfvars"]

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def extract_symbols(self, tree, source: bytes, file_path: str) -> list[dict]:
        # `errors="replace"` cannot raise — every undecodable byte maps to
        # U+FFFD. Earlier `try/except Exception: return []` here was a W907
        # false-hedge; surface real (non-decode) errors instead of silently
        # dropping the whole file.
        text = source.decode("utf-8", errors="replace")

        # .tfvars files: treat each assignment as a variable
        if file_path.endswith(".tfvars"):
            return self._tfvars_symbols(text)

        lines = text.splitlines()
        return self._hcl_symbols(lines, file_path)

    def extract_references(self, tree, source: bytes, file_path: str) -> list[dict]:
        # See extract_symbols for rationale (W907 false-hedge cleanup).
        text = source.decode("utf-8", errors="replace")
        if file_path.endswith(".tfvars"):
            return []
        lines = text.splitlines()
        return self._hcl_refs(lines, file_path)

    # ------------------------------------------------------------------
    # .tfvars
    # ------------------------------------------------------------------

    def _tfvars_symbols(self, text: str) -> list[dict]:
        symbols: list[dict] = []
        for idx, line in enumerate(text.splitlines()):
            if _RE_COMMENT.match(line):
                continue
            m = _RE_TFVARS_ASSIGNMENT.match(line)
            if m:
                symbols.append(
                    self._make_symbol(
                        name=m.group(1),
                        kind="variable",
                        line_start=_line_number(idx),
                        line_end=_line_number(idx),
                        signature=f"{m.group(1)} = ...",
                        visibility="public",
                        is_exported=True,
                    )
                )
        return symbols

    # ------------------------------------------------------------------
    # HCL / Terraform
    # ------------------------------------------------------------------

    def _hcl_symbols(self, lines: list[str], file_path: str) -> list[dict]:
        symbols: list[dict] = []
        in_locals = False
        brace_depth = 0

        for idx, line in enumerate(lines):
            ln = _line_number(idx)
            stripped = line.strip()

            if _RE_COMMENT.match(line):
                continue

            brace_depth = self._update_brace_depth(brace_depth, stripped, ln, file_path)

            in_locals, sym = self._process_line(line, stripped, ln, brace_depth, in_locals)
            if sym:
                symbols.append(sym)

        self._validate_brace_closure(brace_depth, file_path)
        return symbols

    def _update_brace_depth(self, depth: int, stripped: str, ln: int, file_path: str) -> int:
        """Update brace depth with validation guard. Logs error on negative depth (unmatched closing brace)."""
        new_depth = depth + stripped.count("{") - stripped.count("}")
        if new_depth < 0:
            log.error(
                f"Unmatched closing brace in {file_path}:{ln} "
                f"(depth={depth}, line='{stripped[:50]}...')"
            )
            return 0  # Reset to recover gracefully
        return new_depth

    def _validate_brace_closure(self, final_depth: int, file_path: str) -> None:
        """Validate brace balance at EOF. Logs warning if unclosed braces remain."""
        if final_depth > 0:
            log.warning(
                f"Unclosed brace(s) in {file_path}: {final_depth} block(s) left open. "
                f"Symbol extraction may be incomplete."
            )

    def _process_line(
        self, line: str, stripped: str, ln: int, brace_depth: int, in_locals: bool
    ) -> tuple[bool, dict | None]:
        """Process a single line, managing locals-block state. Returns (new_in_locals, symbol)."""
        if in_locals:
            in_locals, sym = self._process_locals(line, ln, brace_depth)
            return (in_locals, sym)

        block_type, sym = self._classify_hcl_block(stripped, ln)
        if block_type == "locals":
            return (True, None)
        return (False, sym)

    def _process_locals(self, line: str, ln: int, brace_depth: int) -> tuple[bool, dict | None]:
        if brace_depth <= 0:
            return (False, None)
        return (True, self._local_key_symbol(line, ln))

    def _classify_hcl_block(self, stripped: str, ln: int) -> tuple[str | None, dict | None]:
        block_type = self._detect_block_type(stripped)
        if block_type == "block2":
            return self._classify_block2(stripped, ln)
        if block_type == "block1":
            return self._classify_block1(stripped, ln)
        if block_type == "block0":
            return self._classify_block0(stripped, ln)
        return (None, None)

    def _detect_block_type(self, stripped: str) -> str | None:
        if _RE_BLOCK2.match(stripped):
            return "block2"
        if _RE_BLOCK1.match(stripped):
            return "block1"
        if _RE_BLOCK0.match(stripped):
            return "block0"
        return None

    def _classify_block2(self, stripped: str, ln: int) -> tuple[str, dict | None]:
        m = _RE_BLOCK2.match(stripped)
        assert m is not None
        sym = self._block2_symbol(m.group(1), m.group(2), m.group(3), ln)
        return ("block2", sym)

    def _classify_block1(self, stripped: str, ln: int) -> tuple[str, dict | None]:
        m = _RE_BLOCK1.match(stripped)
        assert m is not None
        sym = self._block1_symbol(m.group(1), m.group(2), ln)
        return ("block1", sym)

    def _classify_block0(self, stripped: str, ln: int) -> tuple[str | None, dict | None]:
        m = _RE_BLOCK0.match(stripped)
        assert m is not None
        kw = m.group(1)
        if kw == "locals":
            return ("locals", None)
        sym = self._block0_symbol(kw, ln)
        return ("block0", sym)

    def _local_key_symbol(self, line: str, ln: int) -> dict | None:
        m = _RE_LOCAL_KEY.match(line)
        if not m:
            return None
        key = m.group(1)
        return self._make_symbol(
            name=key,
            kind="variable",
            line_start=ln,
            line_end=ln,
            qualified_name=f"local.{key}",
            signature=f"local.{key}",
            visibility="private",
            is_exported=False,
        )

    def _block2_symbol(self, kw: str, label1: str, label2: str, ln: int) -> dict | None:
        if kw in ("resource", "source"):
            qname = f"{label1}.{label2}"
        elif kw == "data":
            qname = f"data.{label1}.{label2}"
        else:
            log.debug("hcl line %d: 2-label block %r not indexed (unrecognized keyword)", ln, kw)
            return None
        return self._make_symbol(
            name=label2,
            kind="class",
            line_start=ln,
            line_end=ln,
            qualified_name=qname,
            signature=f'{kw} "{label1}" "{label2}"',
            visibility="public",
            is_exported=True,
        )

    def _block1_symbol(self, kw: str, label: str, ln: int) -> dict | None:
        if kw not in self._BLOCK1_METADATA:
            return None
        kind, sig_kw, is_exported = self._BLOCK1_METADATA[kw]
        qname = f"var.{label}" if kw == "variable" else label
        return self._make_symbol(
            name=label,
            kind=kind,
            line_start=ln,
            line_end=ln,
            qualified_name=qname,
            signature=f'{sig_kw} "{label}"',
            visibility="public",
            is_exported=is_exported,
        )

    def _block0_symbol(self, kw: str, ln: int) -> dict | None:
        if kw not in self._BLOCK0_METADATA:
            return None
        kind, is_exported = self._BLOCK0_METADATA[kw]
        return self._make_symbol(
            name=kw,
            kind=kind,
            line_start=ln,
            line_end=ln,
            signature=f"{kw} {{}}",
            visibility="public",
            is_exported=is_exported,
        )

    def _hcl_refs(self, lines: list[str], file_path: str) -> list[dict]:
        refs: list[dict] = []
        seen: set[tuple[str, str]] = set()
        current_block: str | None = None

        for idx, line in enumerate(lines):
            ln = _line_number(idx)
            stripped = line.strip()

            if _RE_COMMENT.match(line):
                continue

            current_block = self._update_current_block(stripped, current_block)

            self._extract_all_refs(line, ln, current_block, refs, seen)

        return refs

    def _update_current_block(self, stripped: str, current_block: str | None) -> str | None:
        """Update current block context when a new block declaration is encountered."""
        m = _RE_BLOCK2.match(stripped)
        if m:
            kw, label1, label2 = m.group(1), m.group(2), m.group(3)
            return f"{label1}.{label2}" if kw != "data" else f"data.{label1}.{label2}"
        mb = _RE_BLOCK1.match(stripped)
        if mb:
            return mb.group(2)
        return current_block  # No block change; preserve prior context

    def _extract_all_refs(
        self,
        line: str,
        ln: int,
        current_block: str | None,
        refs: list[dict],
        seen: set[tuple[str, str]],
    ) -> None:
        """Extract all reference types from a single line, guarding against None block."""
        self._add_refs_of_type(
            _RE_VAR_REF.finditer(line),
            lambda m: m.group(1),
            ln,
            current_block,
            refs,
            seen,
        )
        self._add_refs_of_type(
            _RE_MODULE_REF.finditer(line),
            lambda m: m.group(1),
            ln,
            current_block,
            refs,
            seen,
        )
        self._add_refs_of_type(
            _RE_DATA_REF.finditer(line),
            lambda m: f"data.{m.group(1)}.{m.group(2)}",
            ln,
            current_block,
            refs,
            seen,
        )
        self._add_refs_of_type(
            _RE_LOCAL_REF.finditer(line),
            lambda m: m.group(1),
            ln,
            current_block,
            refs,
            seen,
        )
        # resource_type.resource_name with namespace guard
        for m in _RE_RESOURCE_REF.finditer(line):
            ns = m.group(1).split("_")[0]
            if ns not in _BUILTIN_NAMESPACES:
                self._add_ref(
                    m.group(2), ln, current_block, refs, seen
                )

    def _add_refs_of_type(
        self,
        matches,
        extract_name,
        ln: int,
        current_block: str | None,
        refs: list[dict],
        seen: set[tuple[str, str]],
    ) -> None:
        """Add refs of a specific type, applying the name-extraction function."""
        for m in matches:
            target = extract_name(m)
            self._add_ref(target, ln, current_block, refs, seen)

    def _add_ref(
        self,
        target: str,
        ln: int,
        current_block: str | None,
        refs: list[dict],
        seen: set[tuple[str, str]],
    ) -> None:
        """Register a reference, guarding deduplication and source_name validity."""
        key = (target, str(ln))
        if key not in seen:
            seen.add(key)
            refs.append(
                self._make_reference(
                    target_name=target,
                    kind="call",
                    line=ln,
                    source_name=current_block,  # May be None if line precedes any block
                )
            )
