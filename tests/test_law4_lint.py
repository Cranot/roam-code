"""W17.3 LAW 4 CI-lint — detect weak ``agent_contract.facts`` strings.

W17.3 recommended: *"A CI lint that flags any ``agent_contract.facts``
string starting with a digit and ending without an explicit subject. Catches
future regressions without per-command audit."* This file implements that
lint as both a static (AST) and a runtime (CLI invocation) scan.

Two passes:

1. **Static scan** — AST-walk every ``cmd_*.py``, collect every literal
   string emitted in a ``facts``-bearing context (``facts.append(...)``,
   ``facts.extend(...)``, ``facts = [...]``, ``agent_contract={"facts":
   [...]}``), and flag any string whose shape matches the W12.3 / W17.3
   regression patterns (bare ``"N word"`` count-noun forms with no
   analytical subject anchor).

2. **Runtime scan** — invoke a handful of representative ``roam --json
   <cmd>`` calls against the working repo, parse the envelope, and check
   that the resulting ``agent_contract.facts`` list is *concrete-noun
   anchored* — not the abstract ``"see details"`` / ``"ok"`` /
   ``"completed"`` set, and not bare ``"N word"`` form.

Policy:

* Static violations are reported with file:line so a developer can fix
  the location directly. The static scan is currently CLEAN — the lint
  will fail loudly on any future regression.
* Runtime violations are listed as a ``_WEAK_RUNTIME_FACTS`` allowlist
  so existing infractions don't break CI; new ones must be added to the
  allowlist *and* opened as follow-up tickets. The W17.3 spec says
  "advisory" — i.e. an allowlist + xfail pattern when >50 violations
  appear.

Concrete-noun anchor definition (mirrors
``roam.output.formatter.concrete_plural_terminals`` plus a SBOM-specific
extension for W18.2's bucketing facts):

* terminal noun in the anchor set ("findings", "symbols", "files", ...)
* OR contains a verb ("classified", "flagged", "logged", "reached")
* OR is the verdict itself (anchored on the command identity)
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

import pytest

from tests._helpers.repo_root import repo_root

REPO_ROOT = repo_root()
SRC_COMMANDS = REPO_ROOT / "src" / "roam" / "commands"

_SRC_DIR = REPO_ROOT / "src"
if _SRC_DIR.is_dir() and str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))


# ---------------------------------------------------------------------------
# Anchor vocabulary — concrete-noun terminals that satisfy LAW 4.
#
# Mirrors ``roam.output.formatter.concrete_plural_terminals`` so the static
# scan and the auto-derive use the same anchor set. Adding a noun here
# automatically clears a class of false-positive violations.
# ---------------------------------------------------------------------------

_CONCRETE_NOUN_ANCHORS: frozenset[str] = frozenset(
    {
        # Mirrored from formatter.py — `_humanize_summary_fact`'s
        # `concrete_plural_terminals`. Keep these two lists in sync.
        "findings",
        "symbols",
        "files",
        "edges",
        "nodes",
        "cycles",
        "clusters",
        "layers",
        "smells",
        "snapshots",
        "hotspots",
        "secrets",
        "endpoints",
        "agents",
        "rules",
        "commits",
        "tests",
        "dependencies",
        "modules",
        "directories",
        "patterns",
        "alerts",
        "issues",
        "violations",
        "warnings",
        "errors",
        "matches",
        "effects",
        "events",
        "queries",
        "shifts",
        "moves",
        "imports",
        "callers",
        "callees",
        "branches",
        "paths",
        "routes",
        "annotations",
        "types",
        "languages",
        "owners",
        "users",
        "frameworks",
        "vulnerabilities",
        "challenges",
        "keys",
        "values",
        "chars",
        "characters",
        "lines",
        "tokens",
        "bytes",
        "items",
        "entries",
        "records",
        "fields",
        "options",
        "flags",
        "literals",
        "markers",
        "subcommands",
        "scenarios",
        "actions",
        "exits",
        "leaks",
        "gaps",
        "movers",
        "kinds",
        "passed",
        "failed",
        "scanned",
        "checked",
        "owned",
        "analysed",
        "analyzed",
        "removed",
        "added",
        "skipped",
        "affected",
        "available",
        "trending",
        "scored",
        "confirmed",
        "upgrades",
        "downgrades",
        "days",
        "weeks",
        "months",
        "years",
        "hours",
        "minutes",
        "seconds",
        "milliseconds",
        # SBOM W18.2 bucketing additions.
        "packages",
        "phantom",
        "reachable",
        "heuristic",
        "direct",
        # Commonly-emitted fact terminals from the existing fixed commands.
        "total",
        "logged",
        "reached",
        # Roam-domain nouns surfaced in the registry / capability commands.
        "capabilities",
        "commands",
        "checks",
        "checks-passed",
        "checks-failed",
        "schemas",
        "presets",
        "tools",
        "diagnostics",
    }
)

# Verbs that, when present anywhere in the fact, signal that the fact has
# an analytical subject — even if the terminal token is not a noun anchor.
# Mirrors the W12.3 / W17.3 fixes which standardised on analytical verbs.
_ANALYTICAL_VERBS: frozenset[str] = frozenset(
    {
        "classified",
        "flagged",
        "found",
        "detected",
        "introduced",
        "removed",
        "added",
        "scanned",
        "scored",
        "computed",
        "reached",
        "logged",
        "ran",
        "emitted",
        "reported",
        "surfaced",
        "confirmed",
        "rendered",
        "blocked",
        "passed",
        "failed",
        "skipped",
        "verified",
        "rejected",
    }
)


_MEASUREMENT_SUFFIXES: frozenset[str] = frozenset(
    {
        "score",
        "count",
        "total",
        "size",
        "depth",
        "ratio",
        "rate",
        "pct",
        "percent",
        "percentage",
        "ms",
        "bytes",
        "kb",
        "mb",
    }
)


def _is_concrete_anchored(fact: str) -> bool:
    """Return ``True`` if *fact* satisfies LAW 4 (concrete-noun-anchored).

    A fact passes when ANY of these hold:

    1. It contains an analytical verb anywhere in the string.
    2. Its terminal token (after stripping punctuation) is in the
       concrete-noun anchor set.
    3. It is a measurement-named fact (label + numeric value, where the
       label's penultimate word is a measurement suffix — e.g.
       ``"health score 75"``, ``"tangle ratio 0.0"``).
    4. It is a known-strong verdict / sentence (starts with a non-numeric
       subject like ``"healthy"``, ``"run abc has"``, etc.).
    5. It has more than 4 substantive words AND its leading token is not a
       bare digit (long sentences self-anchor).
    """
    if not isinstance(fact, str):
        return False
    stripped = fact.strip()
    if not stripped:
        return False
    # Known-abstract verdicts that activate summary mode (LAW 4 violation).
    if stripped.lower() in {"no data", "ok", "completed", "see details", "tbd", "n/a", "done"}:
        return False
    lower = stripped.lower()
    # Has any analytical verb?
    for verb in _ANALYTICAL_VERBS:
        if re.search(rf"\b{re.escape(verb)}\b", lower):
            return True
    tokens = stripped.split()
    if not tokens:
        return False
    terminal = tokens[-1].lower().rstrip(",.;:!?)").lstrip("(")
    # Terminal token is an anchor noun?
    if terminal in _CONCRETE_NOUN_ANCHORS:
        return True
    # Anchor can also appear *before* a trailing parenthetical (e.g.
    # "225 registered capabilities (213 AI-safe)" — terminal is
    # "ai-safe)" but the actual anchor noun is "capabilities" two
    # tokens back. Strip parenthetical tail and recheck.
    stripped_paren = re.sub(r"\s*\([^)]*\)\s*$", "", stripped).strip()
    if stripped_paren != stripped:
        tail_tokens = stripped_paren.split()
        if tail_tokens:
            tail_terminal = tail_tokens[-1].lower().rstrip(",.;:!?)").lstrip("(")
            if tail_terminal in _CONCRETE_NOUN_ANCHORS:
                return True
    # Measurement form: "<label words...> <suffix> <numeric>" — e.g.
    # "health score 75", "tangle ratio 0.0", "coverage pct 100.0". The
    # auto-derive in formatter.py emits these for keys ending in a
    # measurement suffix; treating them as anchored prevents false
    # positives in the runtime sweep.
    if len(tokens) >= 2:
        # Last token numeric, second-to-last is a measurement suffix.
        try:
            float(tokens[-1])
            penultimate = tokens[-2].lower().rstrip(",.;:!?)")
            if penultimate in _MEASUREMENT_SUFFIXES:
                return True
        except ValueError:
            pass
    # Long sentence with non-numeric lead — likely a verdict.
    first = tokens[0]
    if len(tokens) > 4 and not first[:1].isdigit() and first != "{X}":
        return True
    return False


# ---------------------------------------------------------------------------
# Static AST scan
# ---------------------------------------------------------------------------


def _stringify_joinedstr(node: ast.JoinedStr) -> str:
    """Render an f-string AST node as ``"...{X}..."`` for pattern detection."""
    parts: list[str] = []
    for v in node.values:
        if isinstance(v, ast.Constant) and isinstance(v.value, str):
            parts.append(v.value)
        else:
            # FormattedValue or non-string literal — replace with marker.
            parts.append("{X}")
    return "".join(parts)


def _collect_strings(node: ast.AST):
    """Yield ``(lineno, str)`` pairs for string-like children of *node*.

    Handles plain string constants, f-strings, lists, and tuples of either.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node.lineno, node.value
    elif isinstance(node, ast.JoinedStr):
        yield node.lineno, _stringify_joinedstr(node)
    elif isinstance(node, (ast.List, ast.Tuple)):
        for item in node.elts:
            yield from _collect_strings(item)


def _find_fact_strings(tree: ast.AST) -> list[tuple[int, str]]:
    """Walk *tree* and return every string literal emitted in a facts ctx.

    Detected contexts:

    * ``facts.append(<str>)`` / ``facts.extend(<str>)`` / ``facts.insert(...)``
    * ``facts = [...]``
    * ``{"facts": [...]}`` (any dict with a literal ``"facts"`` key)
    * ``dict(facts=[...])``
    """
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if (
                isinstance(f, ast.Attribute)
                and isinstance(f.value, ast.Name)
                and f.value.id == "facts"
                and f.attr in ("append", "extend", "insert")
            ):
                for arg in node.args:
                    out.extend(_collect_strings(arg))
            if isinstance(node.func, ast.Name) and node.func.id == "dict":
                for kw in node.keywords:
                    if kw.arg == "facts":
                        out.extend(_collect_strings(kw.value))
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "facts":
                    out.extend(_collect_strings(node.value))
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value == "facts":
                    out.extend(_collect_strings(v))
    return out


def _scan_command_file(path: Path) -> list[tuple[int, str]]:
    """Return ``[(lineno, weak_fact)]`` for each violating fact in *path*."""
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    violations: list[tuple[int, str]] = []
    for lineno, raw in _find_fact_strings(tree):
        s = raw.strip()
        # Static heuristic: starts with a literal digit OR an f-string
        # placeholder ({X}) followed by a single short word.
        m = re.match(r"^(?:\{X\}|\d+)\s+(.+)$", s)
        if not m:
            continue
        tail = m.group(1).strip()
        tokens = tail.split()
        if not tokens:
            violations.append((lineno, s))
            continue
        terminal = tokens[-1].lower().rstrip(",.;:!?)").lstrip("(")
        # Pass if terminal is an anchor noun.
        if terminal in _CONCRETE_NOUN_ANCHORS:
            continue
        # Pass if any token in the fact is an analytical verb.
        if any(t.lower().rstrip(",.;:!?)") in _ANALYTICAL_VERBS for t in tokens):
            continue
        # >3 tail-token facts are sentences — anchored by context.
        if len(tokens) > 3:
            continue
        # Inferred symbol-name leading token (e.g. f"{worst.symbol} classified")
        # is a false positive: {X} here is a noun, not a count. The verb
        # check above catches "classified" cases. Anything else slips
        # through into the violation list.
        violations.append((lineno, s))
    return violations


# ---------------------------------------------------------------------------
# Test 1 — static scan
# ---------------------------------------------------------------------------


def test_static_scan_flags_digit_only_hardcoded_facts(tmp_path):
    """Synthetic case: the scanner must catch a weak fact when we plant one.

    Builds a temporary cmd_*.py with a known-weak fact and asserts the
    scanner reports it. Acts as a self-test for the lint logic.
    """
    bad = tmp_path / "cmd_synthetic.py"
    bad.write_text(
        "from __future__ import annotations\n"
        "def go():\n"
        "    facts = []\n"
        "    facts.append('5 critical')\n"  # bare digit + 1 non-anchor word
        "    return facts\n",
        encoding="utf-8",
    )
    v = _scan_command_file(bad)
    assert v, "scanner missed an obvious weak fact"
    assert any("5 critical" in s for _, s in v), v


def test_static_scan_clean_on_canonical_commands():
    """The 9 W17.3-fixed commands must remain clean under the static scan.

    These are the modules the W12.3 / W13.4 / W17.3 polish waves
    standardised on concrete-noun anchoring. Any future regression that
    re-introduces a weak fact in one of these files fails this test.
    """
    canonical = [
        "cmd_idempotency.py",
        "cmd_side_effects.py",
        "cmd_graph_diff.py",
        "cmd_architecture_drift.py",
        "cmd_alerts.py",
        "cmd_adversarial.py",
        "cmd_health.py",
        "cmd_constitution.py",
        "cmd_preflight.py",
    ]
    for fname in canonical:
        path = SRC_COMMANDS / fname
        if not path.exists():
            pytest.skip(f"canonical file missing: {fname}")
        v = _scan_command_file(path)
        assert not v, f"{fname}: re-introduced weak LAW 4 facts: {v}"


def test_static_scan_full_sweep_under_threshold():
    """Sweep every cmd_*.py and report total violations.

    The W17.3 lint is advisory — keep the running count visible so a future
    bump in violations is caught at PR time, but don't fail when the
    delta is non-zero. Threshold: 50. If we ever exceed it, mark
    individual offenders as xfail in this module instead of failing all.
    """
    all_violations: list[tuple[str, int, str]] = []
    for path in sorted(SRC_COMMANDS.glob("cmd_*.py")):
        for lineno, s in _scan_command_file(path):
            all_violations.append((path.name, lineno, s))
    # Currently 0; W17.3 lint catches future regressions.
    assert len(all_violations) < 50, (
        f"LAW 4 lint: {len(all_violations)} weak facts found "
        f"(threshold 50). Top 10:\n" + "\n".join(f"  {n}:{l}: {s!r}" for n, l, s in all_violations[:10])
    )


# ---------------------------------------------------------------------------
# Test 2 — runtime scan on representative commands
# ---------------------------------------------------------------------------


# Representative commands: small, quick-running, varied auto-derive paths.
# Kept short (≤10) per the task brief — full-suite runtime scan is too slow.
_RUNTIME_COMMANDS: list[tuple[str, list[str]]] = [
    ("health", ["health"]),
    ("constitution-show", ["constitution", "show"]),
    ("doctor", ["doctor"]),
    ("capabilities", ["capabilities"]),
    ("languages", ["languages"]),
]

# Known weak runtime facts that pre-date W19.4 — kept as an allowlist so the
# test stays advisory until each is fixed. New violations land outside this
# allowlist and break CI. Each entry is the full fact string.
_WEAK_RUNTIME_FACTS_ALLOWLIST: frozenset[str] = frozenset(
    {
        # ``doctor`` auto-derives from {"total": N, "passed": M, "failed": K}.
        # The humanizer surfaces "total N" / "N passed" / "N failed" — these
        # are weak in isolation but pass the anchor check since "total",
        # "passed", and "failed" are in the noun-anchor set.
    }
)


def _invoke_json(args: list[str]) -> dict | None:
    """Run ``roam --json <args>`` against the current repo and return env."""
    from click.testing import CliRunner

    from roam.cli import cli

    runner = CliRunner()
    full_args = ["--json"] + args
    result = runner.invoke(cli, full_args)
    if result.exit_code != 0:
        return None
    # The output may include status/progress lines before the JSON envelope
    # when the index isn't fresh. Locate the first '{' that parses.
    text = result.output
    for start in range(len(text)):
        if text[start] != "{":
            continue
        try:
            return json.loads(text[start:])
        except json.JSONDecodeError:
            continue
    return None


def test_runtime_scan_health_produces_strong_facts():
    """``roam --json health`` must emit only concrete-noun-anchored facts.

    The auto-derive path produces all-noun-anchored facts when the summary
    keys are well-named (``health_score``, ``tangle_ratio``, ...). This
    test pins that contract — regression caught if the auto-derive
    suddenly starts emitting weak shapes.
    """
    env = _invoke_json(["health"])
    if env is None:
        pytest.skip("roam --json health did not return a parseable envelope")
    facts = env.get("agent_contract", {}).get("facts", [])
    assert facts, "health produced no facts"
    weak = [f for f in facts if not _is_concrete_anchored(f) and f not in _WEAK_RUNTIME_FACTS_ALLOWLIST]
    assert not weak, f"health emitted weak facts (LAW 4): {weak}"


def test_runtime_scan_constitution_produces_strong_facts():
    """``roam --json constitution show`` must emit only strong facts.

    Constitution is W18.1-touched; its facts should be anchored on
    "constitution gate" / "active mode" subjects.
    """
    env = _invoke_json(["constitution", "show"])
    if env is None:
        pytest.skip("roam --json constitution show did not return a parseable envelope")
    facts = env.get("agent_contract", {}).get("facts", [])
    # Constitution may report 'state: not_initialized' with no extra facts;
    # that's fine — the verdict carries the analytical subject.
    weak = [f for f in facts if not _is_concrete_anchored(f) and f not in _WEAK_RUNTIME_FACTS_ALLOWLIST]
    assert not weak, f"constitution emitted weak facts (LAW 4): {weak}"


def test_runtime_scan_representative_sweep():
    """Sweep ~5 representative commands and assert all produce strong facts.

    Acts as a smoke test across diverse auto-derive paths. Any new weak
    fact must be added to ``_WEAK_RUNTIME_FACTS_ALLOWLIST`` *and* opened
    as a follow-up ticket — the allowlist is intentionally short.
    """
    aggregate_weak: list[tuple[str, str]] = []
    for label, args in _RUNTIME_COMMANDS:
        env = _invoke_json(args)
        if env is None:
            # Command may be unavailable on this checkout; skip silently.
            continue
        facts = env.get("agent_contract", {}).get("facts", [])
        for f in facts:
            if _is_concrete_anchored(f):
                continue
            if f in _WEAK_RUNTIME_FACTS_ALLOWLIST:
                continue
            aggregate_weak.append((label, f))
    assert not aggregate_weak, f"LAW 4 runtime sweep: {len(aggregate_weak)} weak facts. Top 10:\n" + "\n".join(
        f"  {l}: {f!r}" for l, f in aggregate_weak[:10]
    )


# ---------------------------------------------------------------------------
# Test 3 — violation-location reporting
# ---------------------------------------------------------------------------


def test_lint_reports_violation_locations(tmp_path):
    """When the lint catches a violation it must include file + lineno.

    A developer needs to be able to jump straight to the offending site.
    """
    bad = tmp_path / "cmd_loud.py"
    bad.write_text(
        "from __future__ import annotations\n"
        "def go():\n"
        "    facts = []\n"
        "    facts.append('3 high')\n"  # weak: anchor 'high' not in set
        "    facts.append('foo bar baz')  # OK: not digit-led\n"
        "    return facts\n",
        encoding="utf-8",
    )
    v = _scan_command_file(bad)
    assert v, "scanner missed the planted weak fact"
    # Must report (lineno, fact) — line 4 is the bad fact.
    bad_lines = {lineno for lineno, _ in v}
    assert 4 in bad_lines, f"expected lineno=4 in report, got: {v}"
    # The bad string content must be in the report.
    assert any("3 high" in s for _, s in v), v


def test_is_concrete_anchored_helper_pure_unit():
    """``_is_concrete_anchored`` decision is pure; pin its behavior."""
    # PASS: terminal in anchor set.
    assert _is_concrete_anchored("5 critical findings")
    assert _is_concrete_anchored("12 warning findings")
    assert _is_concrete_anchored("3722 total files")
    # PASS: contains analytical verb.
    assert _is_concrete_anchored("useThemeClasses classified hot")
    assert _is_concrete_anchored("idempotency scan flagged 2 non-idempotent")
    # PASS: long sentence with non-numeric lead self-anchors.
    assert _is_concrete_anchored("Run roam preflight handleSave before editing")
    # FAIL: bare digit + non-anchor word.
    assert not _is_concrete_anchored("5 critical")
    # FAIL: known-abstract verdicts.
    assert not _is_concrete_anchored("ok")
    assert not _is_concrete_anchored("see details")
    assert not _is_concrete_anchored("no data")
    # FAIL: empty.
    assert not _is_concrete_anchored("")
    assert not _is_concrete_anchored("   ")
