"""YAML language extractor (regex-only, no tree-sitter).

Targets infrastructure YAML files used by CI/CD tools:

* GitLab CI (.gitlab-ci.yml, *.gitlab-ci.yml, *ci.yml with `stages:`)
* GitHub Actions (.github/workflows/*.yml)
* Generic YAML (any .yml/.yaml not matched by the above — top-level keys only)

Symbols extracted
-----------------
GitLab CI
  - Template anchors (top-level keys starting with ".") → kind "class"
  - Job definitions (non-reserved top-level keys)       → kind "function"
  - Stage names (entries under `stages:`)               → kind "constant"

GitHub Actions
  - Workflow name (``name:`` top-level key)             → kind "module"
  - Job names (keys under ``jobs:``)                    → kind "function"
  - Reusable workflow calls (``uses: org/repo/.github/workflows/…``) → kind "class"

Generic YAML
  - Top-level string/number scalar keys                 → kind "variable"

References extracted
--------------------
* ``extends: .template`` / ``extends: [.a, .b]``  → kind "inherits"
* ``!reference [job, section]``                    → kind "call"
* ``needs: [job1, job2]``                          → kind "call"  (GitLab)
* ``needs: job`` (scalar)                          → kind "call"  (GitLab)
* ``uses: org/repo/.github/workflows/wf.yml@ref``  → kind "call"  (GitHub Actions)
"""

from __future__ import annotations

import logging
import os
import re

from .base import LanguageExtractor

log = logging.getLogger(__name__)

# ── Reserved GitLab CI top-level keys (not job names) ────────────────────────
_GITLAB_RESERVED = frozenset({
    "stages", "variables", "default", "workflow", "include",
    "image", "services", "before_script", "after_script",
    "cache", "artifacts", "retry", "timeout", "interruptible",
    "rules", "only", "except", "when", "needs", "allow_failure",
    "coverage", "pages", "release", "environment", "resource_group",
    "parallel", "trigger", "extends", "inherit", "tags", "script",
    "hooks", "secrets", "dast_configuration", "pages:deploy",
})

# ── Regex patterns ────────────────────────────────────────────────────────────

# Top-level key: must start at column 0.
# Allow colons inside the key (e.g. GitLab job names like "test:unit:").
# The lazy quantifier stops at the LAST "colon followed by space/EOL" pair.
_RE_TOP_KEY = re.compile(r'^([A-Za-z0-9_./-][^#\n]*?)\s*:\s*(?:#[^\n]*)?$')

# Stage list entry (inside stages: block): "  - stage_name"
_RE_STAGE_ENTRY = re.compile(r'^\s+-\s+([A-Za-z0-9_-]+)\s*$')

# extends: .template  or  extends: [.a, .b]
_RE_EXTENDS_SCALAR = re.compile(r'extends\s*:\s*([.\w-]+)')
_RE_EXTENDS_LIST = re.compile(r'extends\s*:\s*\[([^\]]+)\]')
_RE_EXTENDS_MULTILINE_ITEM = re.compile(r'^\s+-\s+([.\w-]+)\s*$')

# !reference [job, section]
_RE_REFERENCE = re.compile(r'!reference\s+\[([^\]]+)\]')

# needs: job  or  needs: [j1, j2]
# Job names can contain colons (e.g. GitLab "test:unit")
_RE_NEEDS_SCALAR = re.compile(r'^\s+needs\s*:\s*([A-Za-z0-9_/:-]+)\s*$')
_RE_NEEDS_LIST = re.compile(r'^\s+needs\s*:\s*\[([^\]]+)\]')
_RE_NEEDS_ITEM = re.compile(r'^\s+-\s+(?:job\s*:\s*)?([A-Za-z0-9_/:-]+)\s*$')

# uses: org/repo/.github/workflows/wf.yml@ref  (GitHub Actions)
_RE_USES = re.compile(r'^\s+uses\s*:\s*(.+?)\s*$')

# GitHub Actions: jobs: block starts
_RE_JOBS_KEY = re.compile(r'^jobs\s*:\s*$')
# Under jobs: a job name is indented exactly 2 spaces
_RE_JOB_NAME = re.compile(r'^  ([A-Za-z0-9_-]+)\s*:')

# GitHub Actions workflow-level name
_RE_WF_NAME = re.compile(r'^name\s*:\s*(.+?)\s*$')

# "on:" key (GitHub Actions trigger)
_RE_ON_KEY = re.compile(r'^on\s*:')


def _detect_yaml_flavor(lines: list[str]) -> str:
    """Return 'gitlab', 'github', or 'generic'."""
    has_stages = False
    has_on = False
    has_jobs = False
    for line in lines[:120]:  # scan only the first ~120 lines for speed
        stripped = line.strip()
        if stripped.startswith("stages:"):
            has_stages = True
        if _RE_ON_KEY.match(line):
            has_on = True
        if _RE_JOBS_KEY.match(line):
            has_jobs = True
    if has_on and has_jobs:
        return "github"
    if has_stages:
        return "gitlab"
    return "generic"


def _line_number(lines: list[str], idx: int) -> int:
    """Convert 0-based list index to 1-based line number."""
    return idx + 1


class YamlExtractor(LanguageExtractor):
    """Regex-only extractor for CI/infrastructure YAML files."""

    @property
    def language_name(self) -> str:
        return "yaml"

    @property
    def file_extensions(self) -> list[str]:
        return [".yml", ".yaml"]

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def extract_symbols(self, tree, source: bytes, file_path: str) -> list[dict]:
        try:
            text = source.decode("utf-8", errors="replace")
        except Exception:
            return []
        lines = text.splitlines()
        flavor = _detect_yaml_flavor(lines)

        if flavor == "gitlab":
            return self._gitlab_symbols(lines, file_path)
        if flavor == "github":
            return self._github_symbols(lines, file_path)
        return self._generic_symbols(lines, file_path)

    def extract_references(self, tree, source: bytes, file_path: str) -> list[dict]:
        try:
            text = source.decode("utf-8", errors="replace")
        except Exception:
            return []
        lines = text.splitlines()
        flavor = _detect_yaml_flavor(lines)

        if flavor == "gitlab":
            return self._gitlab_refs(lines, file_path)
        if flavor == "github":
            return self._github_refs(lines, file_path)
        return []

    # ------------------------------------------------------------------
    # GitLab CI
    # ------------------------------------------------------------------

    def _gitlab_symbols(self, lines: list[str], file_path: str) -> list[dict]:
        symbols: list[dict] = []
        in_stages = False

        for idx, line in enumerate(lines):
            ln = _line_number(lines, idx)

            # Detect `stages:` block start
            if line.rstrip() == "stages:":
                in_stages = True
                continue
            # Stage entries
            if in_stages:
                m = _RE_STAGE_ENTRY.match(line)
                if m:
                    symbols.append(self._make_symbol(
                        name=m.group(1),
                        kind="constant",
                        line_start=ln,
                        line_end=ln,
                        signature=f"stage: {m.group(1)}",
                        visibility="public",
                        is_exported=True,
                    ))
                    continue
                # End of stage list when we hit a non-list, non-empty line at col 0
                if line and not line[0].isspace():
                    in_stages = False

            m = _RE_TOP_KEY.match(line)
            if not m:
                continue
            key = m.group(1).strip()
            if key.lower() in _GITLAB_RESERVED:
                continue
            if key.startswith("."):
                # Template anchor
                symbols.append(self._make_symbol(
                    name=key,
                    kind="class",
                    line_start=ln,
                    line_end=ln,
                    signature=f"template: {key}",
                    visibility="public",
                    is_exported=True,
                ))
            else:
                # Job definition
                symbols.append(self._make_symbol(
                    name=key,
                    kind="function",
                    line_start=ln,
                    line_end=ln,
                    signature=f"job: {key}",
                    visibility="public",
                    is_exported=True,
                ))

        return symbols

    def _gitlab_refs(self, lines: list[str], file_path: str) -> list[dict]:
        refs: list[dict] = []
        current_job: str | None = None
        in_extends_list = False
        in_needs_list = False

        for idx, line in enumerate(lines):
            ln = _line_number(lines, idx)

            # Track current job (top-level non-reserved key)
            m = _RE_TOP_KEY.match(line)
            if m:
                key = m.group(1).strip()
                if key.lower() not in _GITLAB_RESERVED and not key.startswith("."):
                    current_job = key
                in_extends_list = False
                in_needs_list = False

            # extends: .template  (scalar)
            m = _RE_EXTENDS_SCALAR.search(line)
            if m and "[" not in line:
                refs.append(self._make_reference(
                    target_name=m.group(1),
                    kind="inherits",
                    line=ln,
                    source_name=current_job,
                ))

            # extends: [.a, .b]
            m = _RE_EXTENDS_LIST.search(line)
            if m:
                for item in m.group(1).split(","):
                    t = item.strip().strip("'\"")
                    if t:
                        refs.append(self._make_reference(
                            target_name=t,
                            kind="inherits",
                            line=ln,
                            source_name=current_job,
                        ))

            # extends: followed by multiline list
            if re.search(r'^\s+extends\s*:\s*$', line):
                in_extends_list = True
            elif in_extends_list:
                m = _RE_EXTENDS_MULTILINE_ITEM.match(line)
                if m:
                    refs.append(self._make_reference(
                        target_name=m.group(1),
                        kind="inherits",
                        line=ln,
                        source_name=current_job,
                    ))
                elif line and not line[0].isspace():
                    in_extends_list = False

            # !reference [job, section]
            for m in _RE_REFERENCE.finditer(line):
                parts = [p.strip() for p in m.group(1).split(",")]
                if parts:
                    refs.append(self._make_reference(
                        target_name=parts[0],
                        kind="call",
                        line=ln,
                        source_name=current_job,
                    ))

            # needs: job  (scalar)
            m = _RE_NEEDS_SCALAR.match(line)
            if m:
                refs.append(self._make_reference(
                    target_name=m.group(1),
                    kind="call",
                    line=ln,
                    source_name=current_job,
                ))

            # needs: [j1, j2]
            m = _RE_NEEDS_LIST.match(line)
            if m:
                for item in m.group(1).split(","):
                    t = item.strip().strip("'\"")
                    if t:
                        refs.append(self._make_reference(
                            target_name=t,
                            kind="call",
                            line=ln,
                            source_name=current_job,
                        ))
                continue

            # needs: followed by multiline list
            if re.match(r'^\s+needs\s*:\s*$', line):
                in_needs_list = True
            elif in_needs_list:
                m = _RE_NEEDS_ITEM.match(line)
                if m:
                    refs.append(self._make_reference(
                        target_name=m.group(1),
                        kind="call",
                        line=ln,
                        source_name=current_job,
                    ))
                elif line and not line[0].isspace():
                    in_needs_list = False

        return refs

    # ------------------------------------------------------------------
    # GitHub Actions
    # ------------------------------------------------------------------

    def _github_symbols(self, lines: list[str], file_path: str) -> list[dict]:
        symbols: list[dict] = []
        in_jobs = False
        stem = os.path.splitext(os.path.basename(file_path))[0]

        for idx, line in enumerate(lines):
            ln = _line_number(lines, idx)

            # Workflow name
            m = _RE_WF_NAME.match(line)
            if m:
                name = m.group(1).strip().strip("'\"")
                if name:
                    symbols.append(self._make_symbol(
                        name=name,
                        kind="module",
                        line_start=ln,
                        line_end=ln,
                        signature=f"workflow: {name}",
                        visibility="public",
                        is_exported=True,
                    ))

            # jobs: block
            if _RE_JOBS_KEY.match(line):
                in_jobs = True
                continue

            if in_jobs:
                # Job name: indented 2 spaces, ends with ":"
                m = _RE_JOB_NAME.match(line)
                if m:
                    job = m.group(1)
                    symbols.append(self._make_symbol(
                        name=job,
                        kind="function",
                        line_start=ln,
                        line_end=ln,
                        signature=f"job: {job}",
                        visibility="public",
                        is_exported=True,
                    ))
                    continue
                # uses: reusable workflow call
                m = _RE_USES.match(line)
                if m:
                    target = m.group(1).strip().strip("'\"")
                    # Extract action name (last path component before @)
                    action_name = target.split("@")[0].split("/")[-1]
                    action_name = os.path.splitext(action_name)[0]
                    if action_name:
                        symbols.append(self._make_symbol(
                            name=action_name,
                            kind="class",
                            line_start=ln,
                            line_end=ln,
                            signature=f"uses: {target}",
                            visibility="public",
                            is_exported=False,
                        ))
                # Back to top level when we hit a key at col 0
                if line and not line[0].isspace():
                    in_jobs = False

        return symbols

    def _github_refs(self, lines: list[str], file_path: str) -> list[dict]:
        refs: list[dict] = []
        in_jobs = False
        current_job: str | None = None
        in_needs_list = False

        for idx, line in enumerate(lines):
            ln = _line_number(lines, idx)

            if _RE_JOBS_KEY.match(line):
                in_jobs = True
                continue

            if in_jobs:
                # Job name
                m = _RE_JOB_NAME.match(line)
                if m:
                    current_job = m.group(1)
                    in_needs_list = False
                    continue

                # uses: reusable workflow
                m = _RE_USES.match(line)
                if m:
                    target = m.group(1).strip().strip("'\"")
                    refs.append(self._make_reference(
                        target_name=target,
                        kind="call",
                        line=ln,
                        source_name=current_job,
                    ))
                    continue

                # needs: (GitHub Actions job dependency)
                m = _RE_NEEDS_SCALAR.match(line)
                if m:
                    refs.append(self._make_reference(
                        target_name=m.group(1),
                        kind="call",
                        line=ln,
                        source_name=current_job,
                    ))
                    continue

                m = _RE_NEEDS_LIST.match(line)
                if m:
                    for item in m.group(1).split(","):
                        t = item.strip().strip("'\"")
                        if t:
                            refs.append(self._make_reference(
                                target_name=t,
                                kind="call",
                                line=ln,
                                source_name=current_job,
                            ))
                    continue

                if re.match(r'^\s+needs\s*:\s*$', line):
                    in_needs_list = True
                elif in_needs_list:
                    m = _RE_NEEDS_ITEM.match(line)
                    if m:
                        refs.append(self._make_reference(
                            target_name=m.group(1),
                            kind="call",
                            line=ln,
                            source_name=current_job,
                        ))
                    if line and line[0] != " ":
                        in_needs_list = False

                if line and not line[0].isspace():
                    in_jobs = False

        return refs

    # ------------------------------------------------------------------
    # Generic YAML (fallback)
    # ------------------------------------------------------------------

    def _generic_symbols(self, lines: list[str], file_path: str) -> list[dict]:
        symbols: list[dict] = []
        for idx, line in enumerate(lines):
            m = _RE_TOP_KEY.match(line)
            if not m:
                continue
            key = m.group(1).strip()
            if not key or key.startswith("#"):
                continue
            symbols.append(self._make_symbol(
                name=key,
                kind="variable",
                line_start=_line_number(lines, idx),
                line_end=_line_number(lines, idx),
                signature=f"{key}:",
                visibility="public",
                is_exported=True,
            ))
        return symbols
