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
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache

from roam._glob_match import _expand_braces

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


def _diff_scan_step_for_added_line_provenance(
    line: str, cur_file: str | None, in_hunk: bool
) -> tuple[str | None, bool, str | None]:
    """Advance diff state while keeping additions tied to their file."""
    if line.startswith("+++ "):
        path = line[4:].strip()
        if path.startswith("b/"):
            path = path[2:]
        return (None if path == "/dev/null" else path), False, None
    if line.startswith("@@"):
        return cur_file, True, None
    if cur_file is None or not in_hunk:
        return cur_file, in_hunk, None
    if line.startswith("+") and not line.startswith("+++"):
        return cur_file, in_hunk, line[1:]
    return cur_file, in_hunk, None


def _added_lines_by_file(diff_text: str) -> dict[str, list[str]]:
    """Parse a unified diff and return per-file added-line lists."""
    out: dict[str, list[str]] = {}
    cur_file: str | None = None
    in_hunk = False
    for line in diff_text.splitlines():
        cur_file, in_hunk, added_line = _diff_scan_step_for_added_line_provenance(line, cur_file, in_hunk)
        if added_line is not None and cur_file is not None:
            out.setdefault(cur_file, []).append(added_line)
    return out


def _fnmatch_brace(name: str, glob: str) -> bool:
    """``fnmatch.fnmatch``, but with ``{a,b}`` alternation expanded first.

    Plain ``fnmatch`` treats ``{``/``}``/``,`` as literal characters, so a
    ``forbidden_target_glob`` like ``crypto/{md5,sha1}`` (shipped in the Go
    starter pack) or ``java.util.{Vector,Hashtable,Stack,Properties}``
    (Java/Kotlin) never matched anything real — same dead-brace defect as
    ``_compile_path_glob`` below, just on the target-name side instead of
    the path side. ``_expand_braces`` is the single shared implementation
    (``roam._glob_match``) so both sides agree on nesting / single-item /
    empty-alternative / unmatched-brace behaviour.
    """
    return any(fnmatch.fnmatch(name, alt) for alt in _expand_braces(glob))


def _match_import_from(line: str, forbidden_glob: str) -> str | None:
    py = _PYTHON_IMPORT_RE.match(line)
    js = _JS_IMPORT_RE.search(line)
    target = ""
    if py:
        target = (py.group(1) or py.group(2) or "").strip()
    elif js:
        target = js.group(1).strip()
    if target and _fnmatch_brace(target, forbidden_glob):
        return target
    return None


def _match_function_call(line: str, forbidden_glob: str) -> str | None:
    stripped = line.lstrip()
    # Definitions aren't calls — skip ``def foo(`` and ``class Foo(``.
    if stripped.startswith(("def ", "class ", "function ", "func ", "async def ")):
        return None
    for m in _FUNCTION_CALL_RE.finditer(line):
        target = m.group(1)
        if _fnmatch_brace(target, forbidden_glob):
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
        if _fnmatch_brace(base, forbidden_glob):
            return base
    return None


def _match_decorator_use(line: str, forbidden_glob: str) -> str | None:
    m = _DECORATOR_RE.match(line)
    if not m:
        return None
    name = m.group(1)
    if _fnmatch_brace(name, forbidden_glob):
        return name
    return None


_PATTERN_MATCHERS = {
    "import_from": _match_import_from,
    "function_call": _match_function_call,
    "class_inherit": _match_class_inherit,
    "decorator_use": _match_decorator_use,
}


@dataclass(frozen=True)
class _RuleHitContext:
    rule_id: str
    severity: str
    description: str
    pattern: str


@dataclass(frozen=True)
class _AddedLineHit:
    path: str
    line: str
    target: str
    added_lines: list[str]
    idx: int


def _capture_hit(rule: _RuleHitContext, hit: _AddedLineHit) -> dict:
    """Record one rule hit with a 5-line context window for reviewers.

    Extracted so the matching pipeline can focus on *which* line matched,
    while this helper owns *how* the evidence is packaged.
    """
    # D6: 5-line context (matched line +/-2 added lines from same file).
    lo = max(0, hit.idx - 2)
    hi = min(len(hit.added_lines), hit.idx + 3)
    context_lines = [hit.added_lines[i].rstrip() for i in range(lo, hi)]
    return {
        "rule_id": rule.rule_id,
        "severity": rule.severity,
        "description": rule.description,
        "pattern": rule.pattern,
        "file": hit.path,
        "matched_import": hit.target,  # legacy name kept for stability
        "matched_target": hit.target,
        "line_excerpt": hit.line.strip()[:120],
        "context_lines": context_lines,
    }


def _prepare_rule(rule: dict) -> tuple[callable, str, str, str, str] | None:
    """Validate and normalize a rule dict for matching.

    Returns ``None`` for rules that should be silently skipped (unknown
    pattern or empty forbidden glob). This keeps the main loop focused on
    walking files/lines instead of guarding rule shape.
    """
    pattern = rule.get("pattern", "")
    matcher = _PATTERN_MATCHERS.get(pattern)
    if matcher is None:
        return None
    source_glob = rule.get("source_glob", "*")
    forbidden_glob = rule.get("forbidden_target_glob", "")
    if not forbidden_glob:
        return None
    severity = (rule.get("severity") or "WARN").upper()
    description = rule.get("description", "")
    return matcher, source_glob, forbidden_glob, severity, description


def _compile_single_glob_regex(glob: str) -> str:
    """Translate ONE brace-free path glob into a regex fragment (no anchors).

    Extracted from ``_compile_path_glob`` so brace alternation (see that
    function) can compile each expansion separately and OR them together.
    All semantics below are unchanged from before the brace fix.

    ``fnmatch`` does not treat ``**`` specially -- it collapses to ``*``, which
    still cannot cross ``/``. So ``**/*.go`` compiles to "one segment, then a
    slash, then *.go" and silently fails to match ``main.go`` at the base level.
    Same for ``src/**/*.py`` against ``src/main.py``.

    That is a silent SCOPE HOLE, not a cosmetic bug: a rule reads as covering a
    whole subtree while exempting everything at the top of it. 124 of the 127
    rules across the seven shipped language packs use ``**``, so every pack had
    a hole at its own base level -- which is where entry points, ``main.go`` and
    top-level modules live.

    SCOPE OF THIS FIX -- deliberately minimal.

    Only ``**/`` changes. ``*`` keeps ``fnmatch``'s behaviour of crossing ``/``,
    because that is what every existing rule was written against. A first
    attempt gave ``*`` POSIX semantics (``[^/]*``, stopping at ``/``), which is
    more correct in the abstract and silently NARROWED every shipped rule:
    ``*.py`` stopped matching ``src/main.py``, and 24 tests failed. Widening a
    scope hole is the bug being fixed here; narrowing every other rule to fix it
    would trade one silent scope change for a larger one.

    Whether ``*`` *should* stop at ``/`` is a real question with its own blast
    radius across 127 shipped rules. It is not this fix.

    Semantics implemented here:
      ``**/``  zero or more path segments (INCLUDING zero -- the fix)
      ``**``   anything, including ``/``
      ``*``    anything, including ``/`` (unchanged from ``fnmatch``)
      ``?``    exactly one character (unchanged from ``fnmatch``)
    """
    out: list[str] = []
    i, n = 0, len(glob)
    while i < n:
        ch = glob[i]
        if glob.startswith("**/", i):
            # The whole fix: the trailing slash is optional, so a base-level
            # file matches. `(?:.*/)?` rather than `(?:[^/]+/)*` keeps `*`'s
            # slash-crossing behaviour consistent across the pattern.
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif ch == "*":
            out.append(".*")  # fnmatch-compatible: crosses `/`
            i += 1
        elif ch == "?":
            out.append(".")  # fnmatch-compatible
            i += 1
        else:
            out.append(re.escape(ch))
            i += 1
    return "".join(out)


@lru_cache(maxsize=512)
def _compile_path_glob(glob: str) -> re.Pattern[str]:
    """Translate a path glob to a regex with CORRECT ``**`` and ``{a,b}`` semantics.

    Brace alternation (shipped 2026-07-27, same defect class as the ``**``
    scope hole documented on ``_compile_single_glob_regex``, one
    metacharacter over): ``{ts,tsx,js,jsx}``-style groups were previously
    ``re.escape``'d as literal text in the loop below, so
    ``src/**/*.{ts,tsx,js,jsx}`` compiled to "a file literally ending in
    the 17-character string ``.{ts,tsx,js,jsx}``" -- which matches nothing
    real. 13 rules in the shipped TypeScript starter pack alone (6 of them
    BLOCK) used that shape and could never fire.

    Fixed by expanding braces BEFORE the per-character loop runs, via the
    shared ``roam._glob_match._expand_braces`` (same expander used by
    ``_fnmatch_brace`` for ``forbidden_target_glob``, so both fields agree
    on nesting / single-item / empty-alternative / unmatched-brace
    behaviour). Each expansion is compiled by
    ``_compile_single_glob_regex`` exactly as a brace-free glob always was
    -- ``*``/``?``/``**`` semantics are untouched -- and the results are
    OR'd into one regex alternation.
    """
    expansions = _expand_braces(glob)
    if len(expansions) == 1:
        body = _compile_single_glob_regex(expansions[0])
    else:
        body = "(?:" + "|".join(_compile_single_glob_regex(e) for e in expansions) + ")"
    return re.compile(body + r"\Z")


def path_matches_glob(path: str, glob: str) -> bool:
    """Match a repo-relative path against a glob, with real ``**`` support.

    Paths are normalised to forward slashes so Windows-style diffs behave the
    same as POSIX ones.
    """
    return bool(_compile_path_glob(glob).match(path.replace("\\", "/")))


def _find_hits(
    matcher: callable,
    forbidden_glob: str,
    source_glob: str,
    added_by_file: dict[str, list[str]],
) -> Iterable[_AddedLineHit]:
    """Yield every line in ``added_by_file`` that ``matcher`` flags.

    Each yielded hit carries the path, line, target, full added-line list, and
    index so the caller can package it with its surrounding context. Isolating
    the search keeps the orchestration function free of file/line iteration.
    """
    for path, added_lines in added_by_file.items():
        if not path_matches_glob(path, source_glob):
            continue
        for idx, line in enumerate(added_lines):
            target = matcher(line, forbidden_glob)
            if target is None:
                continue
            yield _AddedLineHit(
                path=path,
                line=line,
                target=target,
                added_lines=added_lines,
                idx=idx,
            )


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
        prepared = _prepare_rule(rule)
        if prepared is None:
            continue
        matcher, source_glob, forbidden_glob, severity, description = prepared
        rule_hit = _RuleHitContext(
            rule_id=rule.get("id", "<unnamed>"),
            severity=severity,
            description=description,
            pattern=rule.get("pattern", ""),
        )

        for hit in _find_hits(matcher, forbidden_glob, source_glob, added_by_file):
            violations.append(_capture_hit(rule_hit, hit))
    return violations
