"""Mine owner-authored commit messages for recurring engineering evidence.

This is deliberately local and deterministic.  It reads reachable commits from
the checkout's Git object database, assigns body excerpts to semantic defect
families using synonym groups, and writes traceable JSONL artifacts under
``internal/commit-mining`` by default.

Usage::

    python dev/commit_mining.py
    python dev/commit_mining.py --output-dir /tmp/commit-mining

Every extracted row keeps a full SHA, authored timestamp, and a verbatim quote
from the commit body.  The semantic clusters are analyst-defined families, not
exact-string buckets; the matched signal names are included so assignments can
be inspected without trusting the summary prose.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_OUTPUT = _REPO_ROOT / "internal" / "commit-mining"

# These are the owner identities actually present in this clone.  Keep the
# list explicit: it makes the owner/non-owner boundary visible and auditable.
_OWNER_EMAILS = frozenset(
    {
        "44682693+cranot@users.noreply.github.com",
        "mojitogr@gmail.com",
        "cranot@users.noreply.github.com",
        "bonum.galaxy@gmail.com",
        "unionwebapps@gmail.com",
    }
)


@dataclass(frozen=True)
class Commit:
    sha: str
    authored_at: str
    author_name: str
    author_email: str
    message: str

    @property
    def date(self) -> str:
        return self.authored_at[:10]

    @property
    def month(self) -> str:
        return self.authored_at[:7]

    @property
    def subject(self) -> str:
        return self.message.splitlines()[0] if self.message else ""

    @property
    def body(self) -> str:
        lines = self.message.splitlines()
        return "\n".join(lines[1:]) if len(lines) > 1 else ""

    @property
    def owner_authored(self) -> bool:
        return self.author_email.casefold() in _OWNER_EMAILS


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    text: str


def _run_git(repo: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.PIPE)


def _object_commit_count(repo: Path) -> int:
    """Count commit objects, including any that are not reachable from refs."""

    output = _run_git(repo, "cat-file", "--batch-all-objects", "--batch-check=%(objecttype)")
    return sum(line.strip() == b"commit" for line in output.splitlines())


def _load_commits(repo: Path) -> tuple[list[Commit], list[dict[str, str]], int]:
    """Load all reachable commits and retain malformed-record diagnostics."""

    expected = int(_run_git(repo, "rev-list", "--all", "--count").decode().strip())
    raw = _run_git(
        repo,
        "log",
        "--all",
        "--date=iso-strict",
        "--format=%H%x00%aI%x00%an%x00%ae%x00%B%x01",
    )
    commits: list[Commit] = []
    skipped: list[dict[str, str]] = []
    for record_number, record in enumerate(raw.split(b"\x01"), start=1):
        if not record.strip(b"\x00\n\r \t"):
            continue
        # Git retains the newline that follows %B before the record marker;
        # on every record after the first it is therefore the first byte here.
        record = record.lstrip(b"\r\n")
        fields = record.split(b"\x00", 4)
        if len(fields) != 5:
            skipped.append(
                {
                    "reason": "malformed_git_log_record",
                    "record_number": str(record_number),
                    "detail": f"expected 5 fields, got {len(fields)}",
                }
            )
            continue
        try:
            sha, authored_at, author_name, author_email, message = (field.decode("utf-8", "strict") for field in fields)
        except UnicodeDecodeError as exc:
            skipped.append(
                {
                    "reason": "non_utf8_commit_record",
                    "record_number": str(record_number),
                    "detail": str(exc),
                }
            )
            continue
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            skipped.append(
                {
                    "reason": "malformed_sha",
                    "record_number": str(record_number),
                    "detail": sha,
                }
            )
            continue
        commits.append(
            Commit(
                sha=sha,
                authored_at=authored_at,
                author_name=author_name,
                author_email=author_email,
                message=message.rstrip("\n"),
            )
        )
    return commits, skipped, expected


def _trim_span(text: str, start: int, end: int) -> Span | None:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if start == end:
        return None
    return Span(start=start, end=end, text=text[start:end])


def _sentence_spans(text: str) -> list[Span]:
    """Split prose without changing the bytes of any retained quote.

    Commit bodies often use bullets and code snippets, so this is intentionally
    conservative: punctuation and paragraph boundaries split prose, while
    interior whitespace remains part of the quote.
    """

    if not text:
        return []
    spans: list[Span] = []
    start = 0
    boundary = re.compile(r"(?<=[.!?])(?:[ \t]+|\n+)|\n{2,}")
    for match in boundary.finditer(text):
        end = match.start() + (1 if text[match.start() - 1 : match.start()] in ".!?" else 0)
        span = _trim_span(text, start, end)
        if span:
            spans.append(span)
        start = match.end()
    span = _trim_span(text, start, len(text))
    if span:
        spans.append(span)

    # Avoid huge excerpts when a commit contains a long bullet or code block.
    # Line splitting still preserves every quote as an exact body substring.
    result: list[Span] = []
    for span in spans:
        if len(span.text) <= 1200:
            result.append(span)
            continue
        cursor = span.start
        for line in text[span.start : span.end].splitlines(True):
            line_end = cursor + len(line)
            line_span = _trim_span(text, cursor, line_end)
            if line_span:
                result.append(line_span)
            cursor = line_end
    return result


def _has(text: str, patterns: Iterable[str]) -> list[str]:
    return [pattern for pattern in patterns if re.search(pattern, text, re.IGNORECASE | re.DOTALL)]


_FAILURE_SIGNALS = {
    "uncomputable": r"\b(?:cannot|can't|could not|unable|uncomputable|not computable)\b",
    "did_not_run": r"\b(?:did not|does not|never|not)\s+(?:run|execute|scan|read|open|check|measure|reach|match|cover|exercise|fire|work)\b",
    "empty_or_unreadable": r"\b(?:empty (?:stdout|output|result)|unreadable|corrupt|no signal|no measurement|dead pipe|no-op|noop)\b",
    "silent_loss": r"\b(?:silently?|silent[- ](?:pass|success|fallback)|swallow(?:ed)?|dropped|lost|collapsed|clobber(?:ed)?)\b",
    "missing_or_skipped": r"\b(?:missing|missed|skipped|not included|not covered|suppressed|out of scope)\b",
}
_FINE_SIGNALS = {
    "exit_zero": r"\bexit\s*0\b",
    "green_or_pass": r"\b(?:green|pass(?:ed)?|success(?:ful)?|all checks passed)\b",
    "healthy_or_safe": r"\b(?:healthy|safe|clean|fine|no findings|none found|no concerns)\b",
    "zero_result": r"\b(?:0 findings|zero findings|score\s*100|return(?:ed)?\s+0|empty list|none found)\b",
}


def _false_clean(text: str) -> tuple[int, list[str]]:
    explicit = _has(
        text,
        [
            r"false[- ]clean",
            r"green light.{0,100}(?:dead|pipe|never|did not|no work)",
            r"(?:dead|pipe|never|did not|no work).{0,100}green light",
            r"(?:certif(?:y|ied)|reports?\s+(?:safe|healthy|clean)).{0,120}(?:never|not run|did not|unreadable|uncomput|no work)",
        ],
    )
    failure = _has(text, _FAILURE_SIGNALS.values())
    fine = _has(text, _FINE_SIGNALS.values())
    if explicit:
        return 5, ["explicit_false_clean"] + explicit
    if failure and fine:
        return 4, ["failure_signal:" + failure[0], "reassuring_signal:" + fine[0]]
    return 0, []


_CLUSTER_DEFINITIONS = [
    {
        "name": "false_clean_or_uncomputable_as_fine",
        "description": "A missing, failed, or uncomputable signal is represented as a reassuring result.",
        "priority": 100,
    },
    {
        "name": "source_runtime_or_environment_mismatch",
        "description": "Verification and execution use different source, artifact, environment, or checkout state.",
        "priority": 90,
    },
    {
        "name": "structured_signal_loss",
        "description": "A meaningful error, partial, missing, or measured state is dropped into a generic value or envelope.",
        "priority": 80,
    },
    {
        "name": "scope_or_coverage_hole",
        "description": "A scanner, enumerator, test, or gate omits part of the intended surface or analyzes the wrong scope.",
        "priority": 70,
    },
    {
        "name": "silent_fallback_or_swallowed_failure",
        "description": "An exception, unsupported path, or fallback is hidden while downstream output continues as if normal.",
        "priority": 60,
    },
    {
        "name": "contract_namespace_or_metric_drift",
        "description": "Names, fields, parameters, namespaces, or metric meanings diverge across producers and consumers.",
        "priority": 50,
    },
    {
        "name": "stale_state_or_cache_mismatch",
        "description": "Results are computed from stale index, cache, baseline, working-tree, or generated state.",
        "priority": 40,
    },
    {
        "name": "measurement_noise_or_budget_boundary",
        "description": "A noisy, unbounded, timed-out, or resource-capped measurement is treated as reliable.",
        "priority": 30,
    },
    {
        "name": "duplicate_or_non_idempotent_repeat",
        "description": "A repeated import, retry, or write duplicates data or causes unbounded growth.",
        "priority": 20,
    },
]


def _cluster_match(text: str, name: str) -> tuple[int, list[str]]:
    if name == "false_clean_or_uncomputable_as_fine":
        return _false_clean(text)
    if name == "source_runtime_or_environment_mismatch":
        source = _has(
            text,
            [
                r"\b(?:source|checkout|working tree|test suite|library|repo(?:sitory)?)\b",
            ],
        )
        runtime = _has(
            text,
            [
                r"\b(?:compiled|artifact|wheel|installed|global install|deployment|production|runtime|environment|venv)\b",
            ],
        )
        mismatch = _has(
            text,
            [
                r"\b(?:different|mismatch|diverg(?:e|ent)|stale|wrong|not under test|against|rather than|instead of)\b",
            ],
        )
        return (
            3 if source and runtime and mismatch else 0,
            ["source:" + source[0], "runtime:" + runtime[0], "mismatch:" + mismatch[0]]
            if source and runtime and mismatch
            else [],
        )
    if name == "structured_signal_loss":
        loss = _has(
            text,
            [
                r"\b(?:collapsed|dropped|lost|discarded|clobbered|flattened|swallow(?:ed)?|suppressed)\b",
                r"\b(?:return(?:ed)?|floor(?:ed)?|default(?:ed)?|coerce[ds]?|maps?\s+to)\b.{0,100}\b(?:0|zero|none|null|empty|success|safe|clean|generic)\b.{0,80}\b(?:silent|wrong|fail|instead|rather|not)\b",
                r"\b(?:not distinguish|cannot distinguish|no distinction|signal loss|information loss)\b",
            ],
        )
        carrier = _has(
            text,
            [
                r"\b(?:signal|error|finding|verdict|result|envelope|field|state|metric|evidence|disclosure|status)\b",
            ],
        )
        return (3 if loss and carrier else 0, ["loss:" + loss[0], "carrier:" + carrier[0]] if loss and carrier else [])
    if name == "scope_or_coverage_hole":
        scope = _has(
            text,
            [
                r"\b(?:scope|surface|coverage|enumerat(?:e|ed|es)|scan(?:ner|ned)?|check(?:er|ed)?|fixture|workflow|path|glob|namespace|tree)\b",
            ],
        )
        hole = _has(
            text,
            [
                r"\b(?:only\s+part|miss(?:ed|es|ing)|omitted|uncovered|not (?:included|covered|checked|tested)|never (?:executed|scanned|visited)|hole|blind spot|incomplete|wrong scope|unnoticed)\b",
            ],
        )
        return (2 if scope and hole else 0, ["scope:" + scope[0], "hole:" + hole[0]] if scope and hole else [])
    if name == "silent_fallback_or_swallowed_failure":
        explicit_fallback = _has(
            text,
            [
                r"\b(?:swallow(?:ed)?|catch[- ]all|except\s+exception|fallback|no-op|noop)\b",
                r"\b(?:except|error|failure|unsupported|unavailable)\b.{0,100}\b(?:pass|continue|return|default|ignore|skip)\b",
            ],
        )
        silent_bad = _has(text, [r"\bsilent(?:ly)?\b"]) and _has(
            text,
            [
                r"\b(?:error|failure|failed|missing|unreadable|corrupt|wrong|never|not|no findings|zero|empty|disable[sd]?|pass)\b",
            ],
        )
        fallback = explicit_fallback + (["silent_with_adverse_state"] if silent_bad else [])
        consequence = _has(
            text,
            [
                r"\b(?:result|output|verdict|gate|check|signal|evidence|finding|count|score|return)\b",
                r"\b(?:safe|clean|healthy|success|zero|empty|green|pass)\b",
            ],
        )
        return (
            2 if fallback and consequence else 0,
            ["fallback:" + fallback[0], "consequence:" + consequence[0]] if fallback and consequence else [],
        )
    if name == "contract_namespace_or_metric_drift":
        drift = _has(
            text,
            [
                r"\b(?:mismatch|diverg(?:e|ent|ed)|drift|incompatible|wrong (?:name|key|field|path)|renamed?|namespace|polarity|meaning depends|contract|schema|alias)\b",
            ],
        )
        cross_boundary = _has(
            text,
            [
                r"\b(?:consumer|producer|caller|checker|parser|wrapper|command|tool|field|module|import|path|row|cohort|surface)\b",
            ],
        )
        return (
            2 if drift and cross_boundary else 0,
            ["drift:" + drift[0], "boundary:" + cross_boundary[0]] if drift and cross_boundary else [],
        )
    if name == "stale_state_or_cache_mismatch":
        stale = _has(
            text,
            [
                r"\b(?:stale|out[- ]of[- ]date|old|cache|cached|baseline|index|reindex|refresh|working[- ]tree|dirty tree|generated|snapshot|mtime|state)\b",
            ],
        )
        mismatch = _has(
            text,
            [
                r"\b(?:wrong|mismatch|miss(?:ed|ing)|not reflected|does not see|different|recomputed|reused|invalid)\b",
                r"\b(?:before|after)\b.{0,100}\b(?:no|zero|none|found|missing|different|changed)\b",
            ],
        )
        return (
            2 if stale and mismatch else 0,
            ["stale_state:" + stale[0], "state_effect:" + mismatch[0]] if stale and mismatch else [],
        )
    if name == "measurement_noise_or_budget_boundary":
        noise = _has(
            text,
            [
                r"\b(?:noise|noisy|variance|spread|unstable|flaky|single[- ]run|tim(?:e|ed)[- ]out|timeout|budget|cap|bounded|unbounded|latency|cost|resource)\b",
            ],
        )
        reliability = _has(
            text,
            [
                r"\b(?:measure(?:ment|d)?|number|figure|score|result|gate|allow(?:ed)?|reliable|honest|bound)\b",
            ],
        )
        return (
            2 if noise and reliability else 0,
            ["noise_or_budget:" + noise[0], "measurement:" + reliability[0]] if noise and reliability else [],
        )
    if name == "duplicate_or_non_idempotent_repeat":
        repeat = _has(
            text,
            [
                r"\b(?:duplicate|duplicat(?:ed|es|ing)|repeat(?:ed|ing)?|re-?import|re-?run|retry|idempot(?:ent|ency)|double|unbounded growth|row count)\b",
            ],
        )
        effect = _has(text, [r"\b(?:row|entry|data|write|insert|count|growth|accumulat|side effect)\w*\b"])
        return (
            2 if repeat and effect else 0,
            ["repeat:" + repeat[0], "effect:" + effect[0]] if repeat and effect else [],
        )
    return 0, []


def _cluster_segment(span: Span, subject: str) -> tuple[str, list[str], list[str], int] | None:
    text = span.text
    ranked: list[tuple[int, int, str, list[str]]] = []
    for definition in _CLUSTER_DEFINITIONS:
        score, signals = _cluster_match(text, definition["name"])
        if score:
            ranked.append((score, definition["priority"], definition["name"], signals))
    if not ranked:
        return None
    ranked.sort(reverse=True)
    score, _, primary, signals = ranked[0]
    related = [entry[2] for entry in ranked[1:]]
    # A subject can carry the subsystem noun while the exact defect is in the body.
    if subject and subject.casefold() not in text.casefold():
        subject_scores = [_cluster_match(subject, definition["name"])[0] for definition in _CLUSTER_DEFINITIONS]
        if max(subject_scores, default=0) >= 2:
            signals = signals + ["subject_support"]
    return primary, related, signals, score


_PROBLEM_LANGUAGE = re.compile(
    r"\b(?:defect|bug|root cause|false|wrong|never|did not|does not|cannot|unable|uncomput|"
    r"silently|silent|missing|miss(?:ed|es|ing)|dropped|lost|collapsed|swallow|failed|failure|"
    r"raises|empty|unreadable|corrupt|mismatch|diverg|drift|hole|blind|stale|off by|"
    r"not under test|no findings|no-op)\b",
    re.IGNORECASE,
)
_RESOLUTION_LANGUAGE = re.compile(
    r"^(?:now|adds?|fix(?:es|ed)?|wires?|verified|result|left|re-?measured|the (?:catch|gate|scan)|"
    r"suppressed|supported|introduced|surface|full|behavior|three|two|one|all)\b",
    re.IGNORECASE,
)


def _evidence_role(quote: str) -> str:
    """Separate problem-bearing prose from fix/control prose without dropping it."""

    flattened = " ".join(quote.split())
    problem = bool(_PROBLEM_LANGUAGE.search(flattened))
    resolution = bool(_RESOLUTION_LANGUAGE.search(flattened))
    if problem and resolution:
        return "problem_and_resolution"
    if problem:
        return "problem_statement"
    if resolution:
        return "resolution_or_control"
    return "context"


_REFUTATION_MARKERS = [
    r"\brefut(?:ed|e|es|ing|ation)\b",
    r"\bturned out\b",
    r"\bturns out\b",
    r"\bre-?measured\b",
    r"\b(?:was|were)\s+(?:wrong|backwards?|incorrect|off)\b",
    r"\b(?:wrong|off)\s+by\b",
    r"\b(?:backwards?|off by|overestimated|underestimated|mistaken|misread)\b",
    r"\b(?:not what|not the case|rather than expected)\b",
]
_BELIEF_MARKERS = [
    r"\b(?:I|we)\s+(?:thought|expected|assumed|believed|estimated|predicted)\b",
    r"\b(?:the|my|our)\s+(?:theory|hypothesis|assumption|estimate|belief|expectation|prediction)\b",
    r"\b(?:supposedly|supposed to|seemed|looked like|appeared)\b",
]
_TRUTH_MARKERS = [
    r"\b(?:actually|in reality|in fact|measured|observed|found|real(?:ly)?|the truth)\b",
    r"\b(?:turned out|turns out|instead|rather)\b",
]

_CORRECTION_CONTEXT = [
    r"\b(?:the|my|our)\s+(?:theory|hypothesis|assumption|estimate|belief|expectation|prediction)\b",
    r"\b(?:I|we)\s+(?:thought|expected|assumed|believed|estimated|predicted)\b",
    r"\b(?:factual correction|proved wrong|re-?measured|measured against|controlled (?:CI )?experiment)\b",
]


def _evidence_clauses(span: Span, body: str) -> tuple[str | None, str | None]:
    """Return exact belief/truth substrings when a correction has two sides."""

    text = span.text
    # "turned out to be X rather than Y" places the true side before the
    # contrast marker and the discarded belief after it.
    contrast = re.search(r"\b(?:rather than|instead of)\b", text, re.IGNORECASE)
    if contrast:
        before = _trim_span(text, 0, contrast.start())
        after = _trim_span(text, contrast.end(), len(text))
        if before and after:
            if re.search(
                r"\b(?:turned out|turns out|actually|measured|found|observed|real(?:ly)?)\b", before.text, re.IGNORECASE
            ):
                return after.text, before.text
            if re.search(r"\b(?:expected|thought|assumed|believed|theory|hypothesis)\b", after.text, re.IGNORECASE):
                return after.text, before.text

    # "I thought X, but Y" and "X was expected; actually Y".
    contrast = re.search(r"\b(?:but|however|although|yet|actually|in reality|in fact)\b", text, re.IGNORECASE)
    if contrast:
        before = _trim_span(text, 0, contrast.start())
        after = _trim_span(text, contrast.end(), len(text))
        if before and after:
            if re.search(
                r"\b(?:thought|expected|assumed|believed|theory|hypothesis|estimate)\b", before.text, re.IGNORECASE
            ):
                return before.text, after.text

    # The remaining forms explicitly identify a belief but do not state its
    # replacement in a separately bounded clause. Keep that fact visible.
    if _has(text, _BELIEF_MARKERS) or _has(text, [r"\b(?:estimate|theory|hypothesis|assumption)\b"]):
        return text, None
    if _has(text, _TRUTH_MARKERS):
        return None, text
    return None, None


def _refutation_rows(commit: Commit, spans: list[Span]) -> list[dict]:
    rows: list[dict] = []
    for index, span in enumerate(spans):
        marker = _has(span.text, _REFUTATION_MARKERS)
        belief_marker = _has(span.text, _BELIEF_MARKERS)
        truth_marker = _has(span.text, _TRUTH_MARKERS)
        correction_context = _has(span.text, _CORRECTION_CONTEXT)
        if not marker or not (belief_marker or truth_marker or correction_context):
            continue

        context = [span]
        if not belief_marker and index:
            previous = spans[index - 1]
            if _has(previous.text, _BELIEF_MARKERS) or _has(previous.text, _CORRECTION_CONTEXT):
                context.insert(0, previous)
                belief_marker = ["previous_sentence_belief"]
        if not truth_marker and index + 1 < len(spans):
            following = spans[index + 1]
            if _has(following.text, _TRUTH_MARKERS) or _has(
                following.text, [r"\b(?:measured|actual|real|found|observed)\b"]
            ):
                context.append(following)
                truth_marker = ["following_sentence_truth"]

        quote_start = min(item.start for item in context)
        quote_end = max(item.end for item in context)
        body = commit.body
        quote = body[quote_start:quote_end]
        local_span = Span(quote_start, quote_end, quote)
        belief_quote, truth_quote = _evidence_clauses(local_span, body)
        if not belief_quote and belief_marker:
            belief_quote = quote
        if not truth_quote and truth_marker and not belief_quote:
            truth_quote = quote
        if not belief_quote and correction_context:
            # A correction heading or measurement marker is evidence of a
            # revised belief, but the body may not identify the old belief as
            # a separately bounded clause. Preserve the exact whole excerpt
            # and mark the row partial rather than inventing the missing side.
            belief_quote = quote
        rows.append(
            {
                "id": f"{commit.sha}:refutation:{index}",
                "kind": "refutation",
                "sha": commit.sha,
                "date": commit.date,
                "authored_at": commit.authored_at,
                "author_name": commit.author_name,
                "author_email": commit.author_email,
                "quote": quote,
                "belief_quote": belief_quote,
                "truth_quote": truth_quote,
                "evidence_completeness": "complete"
                if belief_quote and truth_quote and belief_quote != truth_quote
                else "partial",
                "matched_markers": marker + belief_marker + truth_marker + correction_context,
            }
        )
    return rows


_NON_ACTION_MARKERS = [
    r"\bdeliberately\s+(?:not|left|kept|static|avoided|omitted)\b",
    r"\b(?:left|leave|leaves|leaving)\s+(?:it|this|that|them|the|existing|the current)\s+(?:alone|unchanged|in place)\b",
    r"\brefus(?:e|ed|es|ing)\s+to\b",
    r"\bflag(?:ged|s)?\s+rather than\b",
    r"\brather than\s+(?:fix|change|remove|rewrite|re-?optim(?:ize|ise)|infer|guess|fail)\b",
    r"\b(?:not|never)\s+(?:fix(?:ed|ing)?|attempt(?:ed|ing)?|change[ds]?|wire[ds]?|re-?optim(?:ize|ise)[ds]?|infer(?:red)?|guess(?:ed)?)\b",
    r"\b(?:out of scope|on purpose|by design|intentionally|explicit opt-in|deferred|parked|tracked separately)\b",
]


def _non_action_rows(commit: Commit, spans: list[Span]) -> list[dict]:
    rows: list[dict] = []
    for index, span in enumerate(spans):
        markers = _has(span.text, _NON_ACTION_MARKERS)
        if not markers:
            continue
        context = [span]
        if index + 1 < len(spans):
            following = spans[index + 1]
            if _has(
                following.text, [r"\b(?:because|so|since|rather|avoid|preserve|leave|scope|cost|risk|reason|instead)\b"]
            ):
                context.append(following)
        body = commit.body
        quote = body[min(item.start for item in context) : max(item.end for item in context)]
        rows.append(
            {
                "id": f"{commit.sha}:non_action:{index}",
                "kind": "deliberate_non_action",
                "sha": commit.sha,
                "date": commit.date,
                "authored_at": commit.authored_at,
                "author_name": commit.author_name,
                "author_email": commit.author_email,
                "quote": quote,
                "decision_quote": span.text,
                "reason_quote": quote if len(context) > 1 else None,
                "matched_markers": markers,
            }
        )
    return rows


_PATH_RE = re.compile(
    r"(?<![\w.-])(?:src|tests|dev|scripts|templates|bench|internal)/[A-Za-z0-9_.@+-]+(?:/[A-Za-z0-9_.@+-]+)*(?:\.[A-Za-z0-9_+-]+)?"
)
_COMMAND_RE = re.compile(r"\broam\s+([a-z][a-z0-9_-]+)\b", re.IGNORECASE)


_SUBSYSTEM_PATTERNS: list[tuple[str, list[str]]] = [
    ("mcp_boundary", [r"\bmcp\b", r"fastmcp", r"receipt", r"wrapper", r"sampling", r"watcher", r"session"]),
    (
        "agent_os_control_plane",
        [
            r"constitution",
            r"\bmode\b",
            r"pr[- ]?bundle",
            r"audit[- ]?trail",
            r"\bleases?\b",
            r"\bmemory\b",
            r"agents?\.md",
            r"\blaws?\b",
        ],
    ),
    (
        "security_and_trust_gates",
        [r"\b(?:vuln|vulnerability|taint|secret|security|reachability|attestation|cosign|supply[- ]chain)\b"],
    ),
    (
        "ci_and_verification",
        [r"\b(?:ci|pytest|test suite|workflow|hook|wheel|release|deployment|venv|installed|production)\b"],
    ),
    ("output_and_envelopes", [r"\b(?:json|sarif|envelope|formatter|verdict|stdout|output|schema)\b"]),
    (
        "index_and_resolution",
        [r"\b(?:index(?:er|ing)?|resolver|resolution|parse|parser|reference|import|relations?|discovery)\b"],
    ),
    ("graph_and_metrics", [r"\b(?:graph|pagerank|cycle|complexity|clone|cluster|centrality|metric|score|coverage)\b"]),
    ("git_scope_and_diffs", [r"\b(?:git|commit|diff|range|working tree|checkout|branch|path|glob|scope)\b"]),
    ("runtime_and_telemetry", [r"\b(?:runtime|trace|telemetry|hotspot|latency|perf(?:ormance)?|benchmark)\b"]),
    ("refactoring_and_codegen", [r"\b(?:refactor|rename|move symbol|codegen|transform)\b"]),
    ("documentation_and_contracts", [r"\b(?:README|CLAUDE|AGENTS|docs?|documentation|description|template)\b"]),
]


def _subsystems(text: str) -> list[str]:
    labels: list[str] = []
    for label, patterns in _SUBSYSTEM_PATTERNS:
        if _has(text, patterns):
            labels.append(label)
    if not labels:
        labels.append("cross_cutting_or_unresolved")
    return labels


def _decorate(row: dict, commit: Commit, source_text: str) -> dict:
    labels = _subsystems(commit.subject + "\n" + source_text)
    row["subsystems"] = labels
    row["primary_subsystem"] = labels[0]
    return row


def _write_jsonl(path: Path, rows: Iterable[dict]) -> int:
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    return count


def _density_rows(owner_commits: list[Commit], defect_rows: list[dict]) -> list[dict]:
    owner_by_month = Counter(commit.month for commit in owner_commits)
    commits_by_subsystem_month: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in defect_rows:
        if row.get("evidence_role") not in {"problem_statement", "problem_and_resolution"}:
            continue
        for subsystem in row["subsystems"]:
            commits_by_subsystem_month[(subsystem, row["date"][:7])].add(row["sha"])

    rows: list[dict] = []
    for subsystem in sorted({key[0] for key in commits_by_subsystem_month}):
        for month in sorted(owner_by_month):
            issue_commits = len(commits_by_subsystem_month.get((subsystem, month), set()))
            if issue_commits == 0:
                continue
            denominator = owner_by_month[month]
            rows.append(
                {
                    "kind": "subsystem_month_density",
                    "subsystem": subsystem,
                    "month": month,
                    "date": f"{month}-01",
                    "sha": sorted(commits_by_subsystem_month[(subsystem, month)]),
                    "source_shas": sorted(commits_by_subsystem_month[(subsystem, month)]),
                    "issue_commits": issue_commits,
                    "owner_commits": denominator,
                    "density_per_100_owner_commits": round(issue_commits * 100 / denominator, 4),
                }
            )
    return rows


def _density_summary(owner_commits: list[Commit], density_rows: list[dict]) -> list[dict]:
    owner_by_month = Counter(commit.month for commit in owner_commits)
    by_subsystem: dict[str, list[dict]] = defaultdict(list)
    for row in density_rows:
        by_subsystem[row["subsystem"]].append(row)
    summaries: list[dict] = []
    for subsystem, rows in by_subsystem.items():
        issue_commits = sum(row["issue_commits"] for row in rows)
        denominator = sum(owner_by_month[row["month"]] for row in rows)
        peak = max(rows, key=lambda row: (row["density_per_100_owner_commits"], row["month"]))
        summaries.append(
            {
                "subsystem": subsystem,
                "issue_commits": issue_commits,
                "months_with_issue_commits": len(rows),
                "owner_commit_month_denominator": denominator,
                "weighted_density_per_100_owner_commits": round(issue_commits * 100 / denominator, 4)
                if denominator
                else 0.0,
                "peak_month": peak["month"],
                "peak_density_per_100_owner_commits": peak["density_per_100_owner_commits"],
            }
        )
    return sorted(
        summaries,
        key=lambda row: (row["weighted_density_per_100_owner_commits"], row["issue_commits"]),
        reverse=True,
    )


def _cluster_counts(rows: list[dict]) -> list[dict]:
    counts: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"rows": set(), "commits": set(), "problem_rows": set(), "problem_commits": set()}
    )
    for row in rows:
        counts[row["cluster"]]["rows"].add(row["id"])
        counts[row["cluster"]]["commits"].add(row["sha"])
        if row.get("evidence_role") in {"problem_statement", "problem_and_resolution"}:
            counts[row["cluster"]]["problem_rows"].add(row["id"])
            counts[row["cluster"]]["problem_commits"].add(row["sha"])
    return [
        {
            "cluster": name,
            "instances": len(values["rows"]),
            "commits": len(values["commits"]),
            "problem_bearing_instances": len(values["problem_rows"]),
            "problem_bearing_commits": len(values["problem_commits"]),
        }
        for name, values in sorted(counts.items(), key=lambda item: (-len(item[1]["problem_commits"]), item[0]))
    ]


def _short_quote(row: dict, limit: int = 360) -> str:
    quote = " ".join(row["quote"].split())
    return quote if len(quote) <= limit else quote[: limit - 1].rstrip() + "…"


def _write_summary(
    path: Path,
    commits: list[Commit],
    owner_commits: list[Commit],
    skipped: list[dict[str, str]],
    expected: int,
    object_commit_count: int,
    defect_rows: list[dict],
    refutation_rows: list[dict],
    non_action_rows: list[dict],
    density_summary: list[dict],
) -> None:
    cluster_counts = _cluster_counts(defect_rows)
    non_owner = [commit for commit in commits if not commit.owner_authored]
    lines = [
        "# Commit-message mining",
        "",
        "This report was generated locally from `git log --all` on the checkout. "
        "Quotes below are exact substrings of commit bodies; labels are semantic families "
        "assigned from synonym groups, not exact-string matches.",
        "",
        "## Corpus accounting",
        "",
        f"- Reachable commits expected from `git rev-list --all`: **{expected}**.",
        f"- Git object database commit objects: **{object_commit_count}**; unreachable commit objects relative to `--all`: **{max(0, object_commit_count - expected)}**.",
        f"- The stated ~10,600-commit corpus is not present in this clone; the verified local corpus is **{expected}** commits.",
        f"- Git-log records parsed: **{len(commits)}**; malformed records skipped: **{len(skipped)}**.",
        f"- Owner-authored commits mined: **{len(owner_commits)}**.",
        f"- Non-owner commits read but excluded from mining: **{len(non_owner)}** "
        "(automation or other authors; see `corpus.json`).",
        f"- Owner commits with an empty body: **{sum(not commit.body.strip() for commit in owner_commits)}** "
        "(processed, but no body excerpt could be extracted).",
        "",
        "Owner identity boundary: the five lowercase email identities observed in this clone "
        "for Cranot (`44682693+Cranot@users.noreply.github.com`, `mojitogr@gmail.com`, "
        "`cranot@users.noreply.github.com`, `bonum.galaxy@gmail.com`, and "
        "`unionwebapps@gmail.com`).",
        "",
        "## Recurring defect families",
        "",
        "Counts show all matched candidate excerpts and the conservative problem-bearing "
        "subset (`evidence_role` = `problem_statement` or `problem_and_resolution`). A commit "
        "can carry more than one family; subsystem density uses distinct problem-bearing "
        "commits per month divided by all owner commits in that month, reported per 100 owner commits.",
        "",
        "| Semantic family | Candidate excerpts | Problem-bearing excerpts | Problem-bearing commits |",
        "|---|---:|---:|---:|",
    ]
    for row in cluster_counts:
        lines.append(
            f"| `{row['cluster']}` | {row['instances']} | {row['problem_bearing_instances']} | "
            f"{row['problem_bearing_commits']} |"
        )
    if not cluster_counts:
        lines.append("| *(none)* | 0 | 0 | 0 |")

    lines += ["", "Representative exact quotes:", ""]
    for cluster in cluster_counts[:8]:
        candidates = (
            [
                row
                for row in defect_rows
                if row["cluster"] == cluster["cluster"] and row.get("evidence_role") == "problem_statement"
            ]
            or [
                row
                for row in defect_rows
                if row["cluster"] == cluster["cluster"]
                and row.get("evidence_role") in {"problem_statement", "problem_and_resolution"}
            ]
            or [row for row in defect_rows if row["cluster"] == cluster["cluster"]]
        )
        representative = max(candidates, key=lambda row: (row["semantic_score"], -len(row["quote"])))
        lines.append(
            f"- `{cluster['cluster']}` — `{representative['sha']}` ({representative['date']}): "
            f"> {_short_quote(representative)}"
        )

    lines += ["", "## Refutations", ""]
    lines.append(
        f"Extracted **{len(refutation_rows)}** refutation candidates from **{len({row['sha'] for row in refutation_rows})}** commits. "
        "`partial` means the commit contains a correction marker but does not provide both a separately identifiable belief and truth quote."
    )
    lines.append("")
    for row in refutation_rows[:12]:
        lines.append(f"- `{row['sha']}` ({row['date']}, {row['evidence_completeness']}): > {_short_quote(row)}")
    if not refutation_rows:
        lines.append("- None extracted (0 candidates).")

    lines += ["", "## Deliberate non-actions", ""]
    lines.append(
        f"Extracted **{len(non_action_rows)}** deliberate non-action candidates from **{len({row['sha'] for row in non_action_rows})}** commits. "
        "The JSONL preserves a decision quote and, when adjacent prose supplies one, a reason quote."
    )
    lines.append("")
    for row in non_action_rows[:12]:
        lines.append(f"- `{row['sha']}` ({row['date']}): > {_short_quote(row)}")
    if not non_action_rows:
        lines.append("- None extracted (0 candidates).")

    lines += ["", "## Subsystem density", ""]
    lines.append(
        "The ranking below is weighted density, not raw issue count. `subsystem_density.jsonl` "
        "contains the month-by-month values, including the denominator used for every row."
    )
    lines.append("")
    lines.append("| Subsystem | Issue commits | Weighted density / 100 owner commits | Peak month | Peak density |")
    lines.append("|---|---:|---:|---|---:|")
    for row in density_summary:
        lines.append(
            f"| `{row['subsystem']}` | {row['issue_commits']} | "
            f"{row['weighted_density_per_100_owner_commits']:.2f} | {row['peak_month']} | "
            f"{row['peak_density_per_100_owner_commits']:.2f} |"
        )
    if not density_summary:
        lines.append("| *(none)* | 0 | 0.00 | — | 0.00 |")

    lines += [
        "",
        "## Artifacts",
        "",
        "- `corpus.json`: verified counts, owner boundary, and skip accounting.",
        "- `cluster_definitions.json`: semantic families and definitions.",
        "- `defect_instances.jsonl`: one exact body excerpt per assigned defect instance.",
        "- `refutations.jsonl`: correction candidates with belief/truth completeness.",
        "- `non_actions.jsonl`: deliberate non-action candidates and adjacent reason quotes.",
        "- `subsystem_density.jsonl`: monthly density with denominators and source SHA lists; aggregate row `date` values are period starts.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def mine(repo: Path, output_dir: Path) -> dict:
    commits, skipped, expected = _load_commits(repo)
    object_commit_count = _object_commit_count(repo)
    owner_commits = [commit for commit in commits if commit.owner_authored]

    defect_rows: list[dict] = []
    refutation_rows: list[dict] = []
    non_action_rows: list[dict] = []
    for commit in owner_commits:
        spans = _sentence_spans(commit.body)
        for span in spans:
            assignment = _cluster_segment(span, commit.subject)
            if assignment:
                cluster, related, signals, score = assignment
                row = {
                    "id": f"{commit.sha}:defect:{span.start}",
                    "kind": "defect_instance",
                    "sha": commit.sha,
                    "date": commit.date,
                    "authored_at": commit.authored_at,
                    "author_name": commit.author_name,
                    "author_email": commit.author_email,
                    "cluster": cluster,
                    "related_clusters": related,
                    "semantic_score": score,
                    "matched_signals": signals,
                    "quote": span.text,
                    "evidence_role": _evidence_role(span.text),
                }
                defect_rows.append(_decorate(row, commit, span.text))
        refutation_rows.extend(_decorate(row, commit, row["quote"]) for row in _refutation_rows(commit, spans))
        non_action_rows.extend(_decorate(row, commit, row["quote"]) for row in _non_action_rows(commit, spans))

    density_rows = _density_rows(owner_commits, defect_rows)
    density_summary = _density_summary(owner_commits, density_rows)

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(output_dir / "defect_instances.jsonl", defect_rows)
    _write_jsonl(output_dir / "refutations.jsonl", refutation_rows)
    _write_jsonl(output_dir / "non_actions.jsonl", non_action_rows)
    _write_jsonl(output_dir / "subsystem_density.jsonl", density_rows)
    (output_dir / "cluster_definitions.json").write_text(
        json.dumps(_CLUSTER_DEFINITIONS, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    non_owner = [commit for commit in commits if not commit.owner_authored]
    skipped_reasons = Counter(item["reason"] for item in skipped)
    corpus = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "repository": str(repo),
        "source_command": "git log --all",
        "reachable_commits_expected": expected,
        "git_object_commit_count": object_commit_count,
        "unreachable_commit_objects": max(0, object_commit_count - expected),
        "git_log_records_parsed": len(commits),
        "malformed_records_skipped": len(skipped),
        "malformed_skip_reasons": dict(sorted(skipped_reasons.items())),
        "commits_processed": len(commits),
        "owner_authored_commits_mined": len(owner_commits),
        "owner_commits_with_empty_body": sum(not commit.body.strip() for commit in owner_commits),
        "non_owner_commits_excluded": len(non_owner),
        "non_owner_authors_excluded": dict(
            sorted(Counter(f"{c.author_name} <{c.author_email}>" for c in non_owner).items())
        ),
        "owner_email_identities": sorted(_OWNER_EMAILS),
        "all_message_bytes_utf8": sum(len(commit.message.encode("utf-8")) for commit in commits),
        "owner_message_bytes_utf8": sum(len(commit.message.encode("utf-8")) for commit in owner_commits),
        "skipped": skipped,
        "artifact_counts": {
            "defect_instances": len(defect_rows),
            "defect_commits": len({row["sha"] for row in defect_rows}),
            "problem_bearing_defect_instances": sum(
                row["evidence_role"] in {"problem_statement", "problem_and_resolution"} for row in defect_rows
            ),
            "problem_bearing_defect_commits": len(
                {
                    row["sha"]
                    for row in defect_rows
                    if row["evidence_role"] in {"problem_statement", "problem_and_resolution"}
                }
            ),
            "refutations": len(refutation_rows),
            "refutation_commits": len({row["sha"] for row in refutation_rows}),
            "deliberate_non_actions": len(non_action_rows),
            "non_action_commits": len({row["sha"] for row in non_action_rows}),
            "subsystem_density_months": len(density_rows),
        },
    }
    (output_dir / "corpus.json").write_text(json.dumps(corpus, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _write_summary(
        output_dir / "SUMMARY.md",
        commits,
        owner_commits,
        skipped,
        expected,
        object_commit_count,
        defect_rows,
        refutation_rows,
        non_action_rows,
        density_summary,
    )
    return corpus


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=_REPO_ROOT, help="Git checkout to mine")
    parser.add_argument("--output-dir", type=Path, default=_DEFAULT_OUTPUT, help="Artifact directory")
    args = parser.parse_args(argv)
    try:
        corpus = mine(args.repo.resolve(), args.output_dir.resolve())
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", "replace").strip() if exc.stderr else str(exc)
        print(f"git command failed: {detail}", file=sys.stderr)
        return 2
    print(
        f"processed={corpus['commits_processed']} owner_mined={corpus['owner_authored_commits_mined']} "
        f"defects={corpus['artifact_counts']['defect_instances']} "
        f"refutations={corpus['artifact_counts']['refutations']} "
        f"non_actions={corpus['artifact_counts']['deliberate_non_actions']} "
        f"output={args.output_dir.resolve()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
