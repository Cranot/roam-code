"""W762 drift-guard - UPPER-case severity literals in ``cmd_*.py``
must not appear inside summary-envelope ``severity`` slots.

Why this drift-guard exists
---------------------------

W547 (the canonical-severity sprint) established the lowercase
4-tier vocabulary ``critical`` / ``error`` / ``warning`` / ``info``
in :mod:`roam.output._severity`. The SARIF contract, the
findings-registry contract, and the cross-detector polarity laws
all source from that module.

W718 + W718-followup-A/B/C cleaned 70+ UPPER-case sites across
``cmd_health`` / ``cmd_pr_risk`` / ``cmd_path_coverage``. W759 /
W760 / W761 target the remaining commands
(``cmd_preflight`` / ``cmd_attest`` / ``cmd_invariants`` /
``cmd_bus_factor`` / ``cmd_complexity``).

Without an AST drift-guard at PR-time, future commands can silently
re-introduce ``"CRITICAL"`` / ``"HIGH"`` / ``"MEDIUM"`` / ``"LOW"``
inside a ``summary`` envelope - the SARIF level-map then
case-folds the label to ``"note"`` and a CI gate that should fire
on ``"critical"`` silently downgrades. The W531 incident
(silently-downgraded CRITICAL -> note) was the original lesson;
this is the W531-class regression prevention layer.

Mirrors the W662 / W606 / W588 / W685 drift-guard pattern: AST
walk + ``_PRE_W762_PENDING`` allowlist of currently-failing sites
with a sister assertion that pending entries must still trip the
detector (catches stealth-cleanups that should drop entries).

What this drift-guard catches
-----------------------------

For every ``src/roam/commands/cmd_*.py`` file:

1. Walk every ``ast.Constant`` whose value is a string in
   :data:`_UPPER_SEVERITY_LITERALS` (``CRITICAL`` / ``HIGH`` /
   ``MEDIUM`` / ``LOW`` / ``WARNING`` / ``INFO`` / ``ERROR``).
2. Classify by context. **BLOCKED**: the literal is the VALUE in
   a dict literal whose KEY constant is ``"severity"``. This is
   the precise shape produced by every ``summary={"severity": ...}``
   envelope assignment + every ``json_envelope(..., severity=...)``
   call that nests a dict. **ALLOWED**: anywhere else (display
   polish like ``{"CRITICAL": "!!"}`` where UPPER is a KEY, rank
   tables, return values from severity-classifier helpers,
   comparisons, ternaries, comments, docstrings).
3. The lint asserts that no NEW blocked site appears beyond the
   :data:`_PRE_W762_PENDING` allowlist.

The strict classifier is the right one: the four pending sites all
flow directly into an envelope's ``severity`` field, which is
exactly where the SARIF level-map applies. Helper returns
(``return "HIGH"``) and rank-table keys (``_SEVERITY_ORDER =
{"CRITICAL": 4, ...}``) are not directly blockable - those are
cleaned by W759-W761 in coordination with the helper-side
migration so the cleanups are atomic per command.

Pre-W762 inventory
------------------

A full AST walk across every ``cmd_*.py`` surfaces exactly 4
blocked sites today, all in ``cmd_preflight.py``:

* L175 ``"severity": "LOW"`` (gate-decision verdict)
* L290 ``"severity": "LOW"`` (no-target safety envelope)
* L312 ``"severity": "LOW"`` (clean-pass envelope)
* L790 ``"severity": fitns.get("severity", "WARNING")`` (fitness
  fall-through default)

Each is gated by the cmd_preflight migration in flight (see
``dev/BACKLOG.md`` W759). The drift-guard pins them in
:data:`_PRE_W762_PENDING` so the lint is shipped fail-loud but
non-blocking until W759 lands.

The "28 / 9 / 10 / 5" inventory in the W762 brief counted ALL
UPPER literals (including helper returns, rank-table keys,
comparison strings) per file - those are the W759-W761
migration target, not the W762 PR-time-prevention scope. This
drift-guard lints the PR-time leak surface (the envelope slot
itself); the wider cleanup is a separate workstream.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests._helpers.repo_root import repo_root

SRC_ROOT = repo_root() / "src" / "roam"
CMD_DIR = SRC_ROOT / "commands"

# ---------------------------------------------------------------------------
# Closed vocabulary: the UPPER-case severity literals W547 deprecated.
# ---------------------------------------------------------------------------
#
# The canonical lowercase vocabulary lives in roam.output._severity:
# critical / error / warning / info. CVSS aliases (high / medium / low)
# resolve to canonical levels via SEVERITY_ALIASES. The UPPER-case
# variants below are the legacy spellings W547 / W531 are migrating
# away from.

_UPPER_SEVERITY_LITERALS: frozenset[str] = frozenset({
    "CRITICAL", "HIGH", "MEDIUM", "LOW",
    "WARNING", "INFO", "ERROR",
})


# ---------------------------------------------------------------------------
# Pre-W762 allowlist: currently-failing sites, grandfathered until the
# coordinated cmd_preflight migration (W759) lands.
# ---------------------------------------------------------------------------
#
# Format: ``"<rel-from-src/roam/commands>:<lineno>": "<rationale>"``.
#
# Drop an entry from this dict the moment its site is cleaned up. The
# ``test_pre_w762_pending_entries_still_have_pattern`` assertion catches
# stale entries automatically.

_PRE_W762_PENDING: dict[str, str] = {
    # cmd_preflight — gate-decision envelopes still emit UPPER pending
    # W759 (canonical-severity migration for cmd_preflight). The W531
    # SARIF downgrade risk is mitigated today by the preflight CLI
    # bypass path; the drift-guard pins the inventory so the
    # follow-up sprint sees an empty allowlist when it ships.
    # W1297: line numbers refreshed after session-introduced cmd_preflight
    # edits shifted the existing sites (~10–65 lines down). The
    # W759-pending migration backlog is unchanged; only the line cursor
    # rebased.
    "cmd_preflight.py:185": (
        "W759-pending: gate-decision verdict envelope; cmd_preflight "
        "canonical-severity migration in flight"
    ),
    "cmd_preflight.py:300": (
        "W759-pending: no-target safety envelope; same migration as L185"
    ),
    "cmd_preflight.py:322": (
        "W759-pending: clean-pass envelope; same migration as L185"
    ),
    "cmd_preflight.py:855": (
        "W759-pending: fitness severity fall-through default; lifts to "
        "canonical 'warning' alongside the helper migration"
    ),
}


# ---------------------------------------------------------------------------
# AST detection
# ---------------------------------------------------------------------------


def _is_blocked_severity_value(
    dict_node: ast.Dict,
    target_ids: set[int],
    target_constants: dict[int, ast.Constant],
) -> None:
    """Walk *dict_node* and tag every UPPER-case severity Constant that
    appears as the VALUE for a literal ``"severity"`` key.

    Mutates *target_ids* + *target_constants* in place — the caller
    aggregates across the whole AST so a Constant tagged via any
    surrounding dict literal counts as blocked.

    The classifier covers three real-world shapes:

    1. Direct literal: ``{"severity": "LOW"}``.
    2. Ternary value: ``{"severity": "HIGH" if cond else "MEDIUM"}``.
    3. Fall-through default in a method call:
       ``{"severity": fitns.get("severity", "WARNING")}``.

    All three flow into the SARIF level-map identically, so all three
    are blocked. Helper returns (``return "HIGH"``) and rank-table
    keys (``_SEVERITY_ORDER = {"CRITICAL": 4}``) are NOT covered here
    — the rank-table case has UPPER as the KEY not the value, and
    helper-return cleanup is W759-W761 scope.
    """
    for k, v in zip(dict_node.keys, dict_node.values):
        if not isinstance(k, ast.Constant):
            continue
        if k.value != "severity":
            continue
        for sub in ast.walk(v):
            if not isinstance(sub, ast.Constant):
                continue
            if not isinstance(sub.value, str):
                continue
            if sub.value not in _UPPER_SEVERITY_LITERALS:
                continue
            target_ids.add(id(sub))
            target_constants[id(sub)] = sub


def _find_blocked_sites(path: Path) -> list[str]:
    """Return ``"<rel-from-commands>:<lineno>"`` for each blocked UPPER literal."""
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return []
    rel = path.relative_to(CMD_DIR).as_posix()
    blocked_ids: set[int] = set()
    blocked_constants: dict[int, ast.Constant] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            _is_blocked_severity_value(node, blocked_ids, blocked_constants)
    return [
        f"{rel}:{blocked_constants[i].lineno}"
        for i in blocked_ids
    ]


def _iter_command_files() -> list[Path]:
    return [
        p
        for p in CMD_DIR.glob("cmd_*.py")
        if "__pycache__" not in p.parts
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_no_new_upper_severity_in_summary_envelope() -> None:
    """No NEW ``{"severity": "CRITICAL|HIGH|MEDIUM|LOW|WARNING|INFO|ERROR"}``
    site may land in ``cmd_*.py``.

    The canonical lowercase vocabulary ``critical`` / ``error`` /
    ``warning`` / ``info`` lives in :mod:`roam.output._severity`.
    Use ``normalize_severity()`` to fold an upstream UPPER label
    onto the canonical set, or emit the canonical literal directly.

    Pre-W762 pending sites are grandfathered in
    :data:`_PRE_W762_PENDING` until W759-W761 land their respective
    command migrations.
    """
    pending = set(_PRE_W762_PENDING)
    violations: list[str] = []
    for path in _iter_command_files():
        for hit in _find_blocked_sites(path):
            if hit in pending:
                continue
            violations.append(hit)
    assert not violations, (
        "W762: UPPER-case severity literal inside a summary envelope's "
        "`severity` slot detected in cmd_*.py. The canonical vocabulary "
        "is lowercase (critical / error / warning / info) per W547. "
        "Use roam.output._severity.normalize_severity() to fold an "
        "upstream UPPER label onto the canonical set, or emit the "
        "canonical literal directly. Offenders:\n  "
        + "\n  ".join(sorted(violations))
    )


def test_pre_w762_pending_entries_actually_exist() -> None:
    """Every ``_PRE_W762_PENDING`` entry must point at a real file.

    Stale entries (the file was deleted or renamed without updating
    this dict) silently widen the allowlist and let real regressions
    through.
    """
    missing: list[str] = []
    for entry in _PRE_W762_PENDING:
        rel, _, _line = entry.partition(":")
        if not (CMD_DIR / rel).exists():
            missing.append(entry)
    assert not missing, (
        f"W762: _PRE_W762_PENDING references missing files: {missing}"
    )


def test_pre_w762_pending_entries_still_have_pattern() -> None:
    """Every ``_PRE_W762_PENDING`` entry must still trip the detector.

    Once a site has been migrated to canonical lowercase, its entry
    must drop from ``_PRE_W762_PENDING`` — otherwise the allowlist
    keeps shielding a file that no longer needs shielding, and a
    future regression in the same file would slip through silently.
    The W662 drift-guard codified this discipline; W762 mirrors it.
    """
    stale: list[str] = []
    # Group pending entries by file for one AST walk per file.
    by_file: dict[str, set[str]] = {}
    for entry in _PRE_W762_PENDING:
        rel, _, _line = entry.partition(":")
        by_file.setdefault(rel, set()).add(entry)

    for rel, expected in by_file.items():
        path = CMD_DIR / rel
        if not path.exists():
            continue  # caught by test_pre_w762_pending_entries_actually_exist
        hits = set(_find_blocked_sites(path))
        for entry in expected:
            if entry not in hits:
                stale.append(entry)

    assert not stale, (
        "W762: _PRE_W762_PENDING entries no longer trip the drift-guard "
        "(the site was migrated). Drop these entries — the allowlist "
        "must stay minimal so genuine regressions surface:\n  "
        + "\n  ".join(sorted(stale))
    )


def test_detector_catches_synthetic_summary_envelope_offender(tmp_path: Path) -> None:
    """The AST detector flags ``summary={"severity": "CRITICAL"}`` — the
    canonical W547 shape.
    """
    src = (
        "def cmd():\n"
        '    return json_envelope("test",\n'
        '        summary={"severity": "CRITICAL"},\n'
        "    )\n"
    )
    offender = tmp_path / "cmd_synthetic.py"
    offender.write_text(src, encoding="utf-8")
    tree = ast.parse(offender.read_text(encoding="utf-8"))
    blocked_ids: set[int] = set()
    blocked_constants: dict[int, ast.Constant] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            _is_blocked_severity_value(node, blocked_ids, blocked_constants)
    assert len(blocked_ids) == 1, (
        f"W762 detector must flag the inline CRITICAL literal; "
        f"got {[blocked_constants[i].value for i in blocked_ids]}"
    )


def test_detector_catches_synthetic_ternary_offender(tmp_path: Path) -> None:
    """Ternary values inside ``{"severity": ...}`` are caught too — the
    SARIF level-map applies regardless of whether the producer was a
    literal or a ternary expression.
    """
    src = (
        "def cmd(cond):\n"
        "    return {\n"
        '        "severity": "HIGH" if cond else "MEDIUM",\n'
        "    }\n"
    )
    offender = tmp_path / "cmd_synthetic_ternary.py"
    offender.write_text(src, encoding="utf-8")
    tree = ast.parse(offender.read_text(encoding="utf-8"))
    blocked_ids: set[int] = set()
    blocked_constants: dict[int, ast.Constant] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            _is_blocked_severity_value(node, blocked_ids, blocked_constants)
    values = sorted(blocked_constants[i].value for i in blocked_ids)
    assert values == ["HIGH", "MEDIUM"], (
        f"W762 detector must flag both ternary branches; got {values}"
    )


def test_detector_ignores_display_polish_icons_dict(tmp_path: Path) -> None:
    """A dict like ``{"CRITICAL": "!!"}`` (UPPER as KEY mapping to a glyph)
    is display polish, not a severity-value emission — NOT flagged.

    The classifier looks only at VALUES under a literal ``"severity"``
    key. ``cmd_complexity``'s ``icons = {"CRITICAL": "!!", "HIGH": "! ",
    "MEDIUM": "~ ", "LOW": "  "}`` table is one such case in the live
    tree.
    """
    src = (
        "def cmd():\n"
        '    icons = {"CRITICAL": "!!", "HIGH": "! ", "MEDIUM": "~ ", "LOW": "  "}\n'
        "    return icons\n"
    )
    offender = tmp_path / "cmd_synthetic_icons.py"
    offender.write_text(src, encoding="utf-8")
    tree = ast.parse(offender.read_text(encoding="utf-8"))
    blocked_ids: set[int] = set()
    blocked_constants: dict[int, ast.Constant] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            _is_blocked_severity_value(node, blocked_ids, blocked_constants)
    assert not blocked_ids, (
        f"W762 detector must NOT flag display-polish icon tables; got "
        f"{[blocked_constants[i].value for i in blocked_ids]}"
    )


def test_detector_ignores_helper_return_values(tmp_path: Path) -> None:
    """Helper returns (``return "HIGH"``) are NOT flagged — that cleanup
    is W759-W761 scope, not W762's PR-time-prevention scope.

    The W762 lint is intentionally narrow: it catches the SARIF
    level-map leak surface (the envelope slot itself). The wider
    helper-return cleanup is coordinated with the canonical-helper
    migration so the per-command cleanup is atomic.
    """
    src = (
        "def classify(score):\n"
        "    if score > 80:\n"
        '        return "CRITICAL"\n'
        "    if score > 50:\n"
        '        return "HIGH"\n'
        '    return "LOW"\n'
    )
    offender = tmp_path / "cmd_synthetic_helper.py"
    offender.write_text(src, encoding="utf-8")
    tree = ast.parse(offender.read_text(encoding="utf-8"))
    blocked_ids: set[int] = set()
    blocked_constants: dict[int, ast.Constant] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            _is_blocked_severity_value(node, blocked_ids, blocked_constants)
    assert not blocked_ids, (
        f"W762 detector must NOT flag helper return values; got "
        f"{[blocked_constants[i].value for i in blocked_ids]}"
    )


def test_detector_ignores_string_literal_mentions(tmp_path: Path) -> None:
    """Docstring / string-literal mentions of UPPER labels must NOT be
    flagged — the AST walks expression nodes, not string contents.
    """
    src = (
        '"""This module discusses the legacy ``CRITICAL`` / ``HIGH`` labels."""\n'
        'DOC = "severity values: CRITICAL, HIGH, MEDIUM, LOW"\n'
    )
    offender = tmp_path / "cmd_synthetic_doc.py"
    offender.write_text(src, encoding="utf-8")
    tree = ast.parse(offender.read_text(encoding="utf-8"))
    blocked_ids: set[int] = set()
    blocked_constants: dict[int, ast.Constant] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            _is_blocked_severity_value(node, blocked_ids, blocked_constants)
    assert not blocked_ids, (
        f"W762 detector must ignore docstring / string-literal mentions; "
        f"got {[blocked_constants[i].value for i in blocked_ids]}"
    )


def test_w547_canonical_vocabulary_stays_stable() -> None:
    """Pin the lowercase 4-tier W762 migrates toward.

    If the canonical vocabulary changes shape (W547 superseded by a
    different scheme), this test fails loudly so the W762 lint can be
    re-cut against the new contract instead of silently drifting.
    """
    from roam.output._severity import SEVERITY_LEVELS

    assert SEVERITY_LEVELS == frozenset(
        {"critical", "error", "warning", "info"}
    ), (
        "W762: canonical lowercase 4-tier (W547) has drifted — "
        "re-cut the W762 drift-guard against the new vocabulary."
    )
