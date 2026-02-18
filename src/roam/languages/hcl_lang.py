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

# ── Regex patterns ────────────────────────────────────────────────────────────

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
    r'^([a-z_]+)\s*\{',
)

# locals { … } contents — key = value  inside a locals block
_RE_LOCAL_KEY = re.compile(r'^\s+([a-z_][a-zA-Z0-9_]*)\s*=')

# Reference patterns (applied to every line)
_RE_VAR_REF = re.compile(r'\bvar\.([a-z_][a-zA-Z0-9_-]*)\b')
_RE_MODULE_REF = re.compile(r'\bmodule\.([a-z_][a-zA-Z0-9_-]*)(?:\.([a-z_][a-zA-Z0-9_-]*))?\b')
_RE_DATA_REF = re.compile(r'\bdata\.([a-z_][a-zA-Z0-9_-]*)\.([a-z_][a-zA-Z0-9_-]*)\b')
_RE_LOCAL_REF = re.compile(r'\blocal\.([a-z_][a-zA-Z0-9_-]*)\b')

# Resource self-references: aws_vpc.main, google_compute_instance.vm etc.
# Must look like <provider_resource_type>.<name> where type contains "_"
_RE_RESOURCE_REF = re.compile(r'\b([a-z][a-z0-9]*_[a-z][a-zA-Z0-9_]*)\.([a-z_][a-zA-Z0-9_-]*)\b')

# Built-in namespaces to skip when scanning resource refs
_BUILTIN_NAMESPACES = frozenset({
    "var", "module", "data", "local", "each", "count",
    "path", "terraform", "self", "null",
})

# HCL comment markers
_RE_COMMENT = re.compile(r'^\s*(?:#|//)')

# Detect .tfvars files (key = value only, no block declarations)
_RE_TFVARS_ASSIGNMENT = re.compile(r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*=')


def _line_number(idx: int) -> int:
    return idx + 1


class HclExtractor(LanguageExtractor):
    """Regex-only extractor for HCL / Terraform configuration files."""

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
        try:
            text = source.decode("utf-8", errors="replace")
        except Exception:
            return []

        # .tfvars files: treat each assignment as a variable
        if file_path.endswith(".tfvars"):
            return self._tfvars_symbols(text)

        lines = text.splitlines()
        return self._hcl_symbols(lines, file_path)

    def extract_references(self, tree, source: bytes, file_path: str) -> list[dict]:
        try:
            text = source.decode("utf-8", errors="replace")
        except Exception:
            return []
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
                symbols.append(self._make_symbol(
                    name=m.group(1),
                    kind="variable",
                    line_start=_line_number(idx),
                    line_end=_line_number(idx),
                    signature=f"{m.group(1)} = ...",
                    visibility="public",
                    is_exported=True,
                ))
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

            # Track brace depth to know when we leave a locals block
            opens = stripped.count("{")
            closes = stripped.count("}")
            brace_depth += opens - closes

            # -- locals { key = value } --
            if in_locals:
                if brace_depth <= 0:
                    in_locals = False
                else:
                    m = _RE_LOCAL_KEY.match(line)
                    if m:
                        key = m.group(1)
                        symbols.append(self._make_symbol(
                            name=key,
                            kind="variable",
                            line_start=ln,
                            line_end=ln,
                            qualified_name=f"local.{key}",
                            signature=f"local.{key}",
                            visibility="private",
                            is_exported=False,
                        ))
                continue

            # Two-label block: resource "type" "name" {
            m = _RE_BLOCK2.match(stripped)
            if m:
                kw, label1, label2 = m.group(1), m.group(2), m.group(3)
                if kw in ("resource", "source"):
                    symbols.append(self._make_symbol(
                        name=label2,
                        kind="class",
                        line_start=ln,
                        line_end=ln,
                        qualified_name=f"{label1}.{label2}",
                        signature=f'{kw} "{label1}" "{label2}"',
                        visibility="public",
                        is_exported=True,
                    ))
                elif kw == "data":
                    symbols.append(self._make_symbol(
                        name=label2,
                        kind="class",
                        line_start=ln,
                        line_end=ln,
                        qualified_name=f"data.{label1}.{label2}",
                        signature=f'data "{label1}" "{label2}"',
                        visibility="public",
                        is_exported=True,
                    ))
                continue

            # One-label block: variable/output/module/provider/job/task/group
            m = _RE_BLOCK1.match(stripped)
            if m:
                kw, label = m.group(1), m.group(2)
                if kw in ("variable",):
                    symbols.append(self._make_symbol(
                        name=label,
                        kind="variable",
                        line_start=ln,
                        line_end=ln,
                        qualified_name=f"var.{label}",
                        signature=f'variable "{label}"',
                        visibility="public",
                        is_exported=True,
                    ))
                elif kw in ("output",):
                    symbols.append(self._make_symbol(
                        name=label,
                        kind="function",
                        line_start=ln,
                        line_end=ln,
                        signature=f'output "{label}"',
                        visibility="public",
                        is_exported=True,
                    ))
                elif kw in ("module",):
                    symbols.append(self._make_symbol(
                        name=label,
                        kind="module",
                        line_start=ln,
                        line_end=ln,
                        signature=f'module "{label}"',
                        visibility="public",
                        is_exported=True,
                    ))
                elif kw in ("provider",):
                    symbols.append(self._make_symbol(
                        name=label,
                        kind="module",
                        line_start=ln,
                        line_end=ln,
                        signature=f'provider "{label}"',
                        visibility="public",
                        is_exported=False,
                    ))
                elif kw in ("job", "task", "group", "build"):
                    symbols.append(self._make_symbol(
                        name=label,
                        kind="function",
                        line_start=ln,
                        line_end=ln,
                        signature=f'{kw} "{label}"',
                        visibility="public",
                        is_exported=True,
                    ))
                continue

            # No-label block: terraform {, locals {, build {
            m = _RE_BLOCK0.match(stripped)
            if m:
                kw = m.group(1)
                if kw == "locals":
                    in_locals = True
                elif kw == "terraform":
                    symbols.append(self._make_symbol(
                        name="terraform",
                        kind="module",
                        line_start=ln,
                        line_end=ln,
                        signature="terraform {}",
                        visibility="public",
                        is_exported=False,
                    ))
                elif kw == "build":
                    symbols.append(self._make_symbol(
                        name="build",
                        kind="function",
                        line_start=ln,
                        line_end=ln,
                        signature="build {}",
                        visibility="public",
                        is_exported=True,
                    ))

        return symbols

    def _hcl_refs(self, lines: list[str], file_path: str) -> list[dict]:
        refs: list[dict] = []
        seen: set[tuple[str, str]] = set()
        current_block: str | None = None

        for idx, line in enumerate(lines):
            ln = _line_number(idx)
            stripped = line.strip()

            if _RE_COMMENT.match(line):
                continue

            # Track current block for source_name context
            m = _RE_BLOCK2.match(stripped)
            if m:
                kw, label1, label2 = m.group(1), m.group(2), m.group(3)
                current_block = f"{label1}.{label2}" if kw != "data" else f"data.{label1}.{label2}"
            else:
                mb = _RE_BLOCK1.match(stripped)
                if mb:
                    current_block = mb.group(2)

            def _add(target: str):
                key = (target, str(ln))
                if key not in seen:
                    seen.add(key)
                    refs.append(self._make_reference(
                        target_name=target,
                        kind="call",
                        line=ln,
                        source_name=current_block,
                    ))

            # var.name
            for m in _RE_VAR_REF.finditer(line):
                _add(m.group(1))

            # module.name  or  module.name.output
            for m in _RE_MODULE_REF.finditer(line):
                mod = m.group(1)
                _add(mod)

            # data.type.name
            for m in _RE_DATA_REF.finditer(line):
                _add(f"data.{m.group(1)}.{m.group(2)}")

            # local.name
            for m in _RE_LOCAL_REF.finditer(line):
                _add(m.group(1))

            # resource_type.resource_name  (e.g. aws_vpc.main)
            for m in _RE_RESOURCE_REF.finditer(line):
                ns = m.group(1).split("_")[0]
                if ns not in _BUILTIN_NAMESPACES:
                    _add(m.group(2))

        return refs
