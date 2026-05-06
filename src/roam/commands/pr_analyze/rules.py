"""Rules-pattern matching for ``roam pr-analyze`` (D5 split).

Carries:

* :func:`_added_lines_by_file` — unified-diff parser that returns the
  added-line lists per file. Used by both the rules check and by the
  AI-scoring signals upstream.
* The four pattern matchers (``import_from``, ``function_call``,
  ``class_inherit``, ``decorator_use``) and the dispatch dict
  ``_PATTERN_MATCHERS`` they register into.
* :func:`_check_rules` — the matcher loop that turns ``rules.yml``
  entries into structured violation dicts (with the D6 5-line
  ``context_lines`` block attached to each hit).

The import regexes live here too because they're shared with both the
matcher and the AI-scoring orphan-imports signal — keeping a single
authoritative definition prevents drift.
"""

from __future__ import annotations

import fnmatch
import re

# Shared with cmd_pr_analyze AI-scoring (orphan-imports signal). Single
# source-of-truth so the matcher and the scorer can't disagree on which
# strings count as import lines.
_PYTHON_IMPORT_RE = re.compile(r"^\s*(?:from\s+(\S+)\s+import|\s*import\s+(\S+))")
_JS_IMPORT_RE = re.compile(r"""import\s+.*?from\s+['"]([^'"]+)['"]""")

_CLASS_INHERIT_RE = re.compile(r"^\s*class\s+\w+\s*\(([^)]+)\)")
_DECORATOR_RE = re.compile(r"^\s*@([\w.]+)")
# Function-call detection — names plus optional dotted attribute path. Skips
# definition lines (def/class) by checking the line doesn't start with them.
_FUNCTION_CALL_RE = re.compile(r"(?<!def\s)(?<!class\s)\b([\w.]+)\s*\(")


def _added_lines_by_file(diff_text: str) -> dict[str, list[str]]:
    """Parse a unified diff and return per-file added-line lists."""
    out: dict[str, list[str]] = {}
    cur_file: str | None = None
    in_hunk = False
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip()
            if path.startswith("b/"):
                path = path[2:]
            cur_file = path if path != "/dev/null" else None
            in_hunk = False
            continue
        if line.startswith("@@"):
            in_hunk = True
            continue
        if cur_file is None or not in_hunk:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            out.setdefault(cur_file, []).append(line[1:])
    return out


def _match_import_from(line: str, forbidden_glob: str) -> str | None:
    py = _PYTHON_IMPORT_RE.match(line)
    js = _JS_IMPORT_RE.search(line)
    target = ""
    if py:
        target = (py.group(1) or py.group(2) or "").strip()
    elif js:
        target = js.group(1).strip()
    if target and fnmatch.fnmatch(target, forbidden_glob):
        return target
    return None


def _match_function_call(line: str, forbidden_glob: str) -> str | None:
    stripped = line.lstrip()
    # Definitions aren't calls — skip ``def foo(`` and ``class Foo(``.
    if stripped.startswith(("def ", "class ", "function ", "func ", "async def ")):
        return None
    for m in _FUNCTION_CALL_RE.finditer(line):
        target = m.group(1)
        if fnmatch.fnmatch(target, forbidden_glob):
            return target
    return None


def _match_class_inherit(line: str, forbidden_glob: str) -> str | None:
    m = _CLASS_INHERIT_RE.match(line)
    if not m:
        return None
    for raw_base in m.group(1).split(","):
        base = raw_base.strip().split("=", 1)[0].strip()  # strip kwargs like metaclass=X
        if not base:
            continue
        if fnmatch.fnmatch(base, forbidden_glob):
            return base
    return None


def _match_decorator_use(line: str, forbidden_glob: str) -> str | None:
    m = _DECORATOR_RE.match(line)
    if not m:
        return None
    name = m.group(1)
    if fnmatch.fnmatch(name, forbidden_glob):
        return name
    return None


_PATTERN_MATCHERS = {
    "import_from": _match_import_from,
    "function_call": _match_function_call,
    "class_inherit": _match_class_inherit,
    "decorator_use": _match_decorator_use,
}


def _check_rules(diff_text: str, rules: list[dict]) -> list[dict]:
    """Match each rule against the diff.

    v1.1 supports four pattern types via ``_PATTERN_MATCHERS``:

    * ``import_from`` — Python ``from X import`` / ``import X`` and
      JS/TS ``import ... from "X"`` whose target matches the forbidden glob.
    * ``function_call`` — any call ``name(`` or ``ns.name(`` whose
      qualified name matches (e.g. ``os.system``, ``eval``, ``pickle.loads``).
    * ``class_inherit`` — a class declaration whose base list contains a
      forbidden base (e.g. ``class Foo(DangerousMixin)``).
    * ``decorator_use`` — a decorator line ``@name`` or ``@ns.name``
      matching the forbidden glob (e.g. ``@deprecated``, ``@unsafe.*``).

    Each violation carries a 5-line ``context_lines`` block centred on the
    matched line so downstream renderers (D6) can show reviewers what
    changed without an extra git fetch.

    Unknown pattern names are skipped silently so future rule files
    don't crash older Roam clients.
    """
    if not diff_text or not rules:
        return []

    added_by_file = _added_lines_by_file(diff_text)
    violations: list[dict] = []

    for rule in rules:
        rule_id = rule.get("id", "<unnamed>")
        pattern = rule.get("pattern", "")
        matcher = _PATTERN_MATCHERS.get(pattern)
        if matcher is None:
            continue
        source_glob = rule.get("source_glob", "*")
        forbidden_glob = rule.get("forbidden_target_glob", "")
        severity = (rule.get("severity") or "WARN").upper()
        description = rule.get("description", "")
        if not forbidden_glob:
            continue

        for path, added_lines in added_by_file.items():
            if not fnmatch.fnmatch(path, source_glob):
                continue
            for idx, line in enumerate(added_lines):
                target = matcher(line, forbidden_glob)
                if target is None:
                    continue
                # D6: 5-line context (matched line +/-2 added lines from same file).
                lo = max(0, idx - 2)
                hi = min(len(added_lines), idx + 3)
                context_lines = [added_lines[i].rstrip() for i in range(lo, hi)]
                violations.append(
                    {
                        "rule_id": rule_id,
                        "severity": severity,
                        "description": description,
                        "pattern": pattern,
                        "file": path,
                        "matched_import": target,  # legacy name kept for stability
                        "matched_target": target,
                        "line_excerpt": line.strip()[:120],
                        "context_lines": context_lines,
                    }
                )
    return violations
